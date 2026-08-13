"""HealthImportService — orchestrates the Apple Health ingestion pipeline."""

from __future__ import annotations

import itertools
import logging

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.health_importers import (
    HealthSampleImporter,
    _check_sample_exists_clause,
)
from snore.database.models import HealthSample
from snore.database.session import session_scope
from snore.database.txn import run_txn
from snore.database.write_gate import write_gate
from snore.parsers.apple_health import hae_json, xml_reader
from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.parser import AppleHealthParser
from snore.services.schemas import HealthImportResult

logger = logging.getLogger(__name__)

__all__ = ["HealthImportService"]


class HealthImportService:
    """Orchestrates the Apple Health import pipeline for file and HAE-push channels."""

    async def import_file(
        self,
        path: Path,
        profile_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int | None = None,
        batch_size: int = 500,
        dry_run: bool = False,
        progress_callback: Callable[[int], None] | None = None,
    ) -> HealthImportResult:
        """Import an Apple Health export file (zip or directory).

        Args:
            path: Path to an ``export.zip`` or directory containing ``export.xml``.
            profile_id: Target profile for the imported samples.
            date_from: If set, only import records with ``night_date >= date_from``.
            date_to: If set, only import records with ``night_date <= date_to``.
            limit: Maximum number of records to import after date filtering.
            batch_size: Records per write transaction chunk (default 500).
            dry_run: When True, count would-be inserts without writing anything.
            progress_callback: Called with total records processed after each batch.
        """
        if not AppleHealthParser().detect(path):
            raise ValueError(
                f"Path is not a supported Apple Health export (no export.xml found): {path}"
            )

        skip_counter: dict[str, int] = {}
        records_iter = xml_reader.iter_records(
            path,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            skip_counter=skip_counter,
        )

        inserted, skipped, nights_recomputed = await self._import_records(
            records_iter, profile_id, batch_size, dry_run, progress_callback
        )

        return HealthImportResult(
            inserted=inserted,
            skipped=skipped,
            unknown_metrics=skip_counter,
            malformed_points=0,
            nights_recomputed=nights_recomputed,
            dry_run=dry_run,
        )

    async def import_payload(
        self,
        payload: dict[str, object],
        profile_id: int,
    ) -> HealthImportResult:
        """Import a Health Auto Export (HAE) JSON push payload.

        Args:
            payload: Decoded JSON body, e.g. ``{"data": {"metrics": [...]}}``.
            profile_id: Target profile for the imported samples.
        """
        parse_result = hae_json.parse_payload(payload)

        inserted, skipped, nights_recomputed = await self._import_records(
            iter(parse_result.records), profile_id, 500, False, None
        )

        return HealthImportResult(
            inserted=inserted,
            skipped=skipped,
            unknown_metrics=parse_result.unknown_metrics,
            malformed_points=parse_result.skipped_points,
            nights_recomputed=nights_recomputed,
            dry_run=False,
        )

    async def _import_records(
        self,
        records_iter: Iterator[RawHealthRecord],
        profile_id: int,
        batch_size: int,
        dry_run: bool,
        progress_callback: Callable[[int], None] | None,
    ) -> tuple[int, int, int]:
        """Consume records_iter in batches; persist or dry-run count each batch.

        Returns (inserted, skipped, nights_recomputed).
        """
        inserted_total = 0
        skipped_total = 0
        affected_nights: set[date] = set()
        processed = 0

        importer = HealthSampleImporter()

        for _chunk_num in itertools.count(1):
            chunk = list(itertools.islice(records_iter, batch_size))
            if not chunk:
                break

            processed += len(chunk)

            if dry_run:
                new, dup = await _count_new_vs_dup(chunk, profile_id)
                inserted_total += new
                skipped_total += dup
            else:

                async def _insert(
                    db: AsyncSession,
                    *,
                    _c: list[RawHealthRecord] = chunk,
                    _i: HealthSampleImporter = importer,
                ) -> tuple[int, int, set[date]]:
                    return await _i.insert_samples_batch(_c, profile_id, db)

                async with write_gate():
                    ci, cs, nights = await run_txn(_insert)
                inserted_total += ci
                skipped_total += cs
                affected_nights.update(nights)

            if progress_callback is not None:
                progress_callback(processed)

        if not dry_run and affected_nights:
            for night in sorted(affected_nights):

                async def _recompute(
                    db: AsyncSession,
                    *,
                    _n: date = night,
                    _i: HealthSampleImporter = importer,
                ) -> None:
                    await _i.recompute_nightly_summary(profile_id, _n, db)

                async with write_gate():
                    await run_txn(_recompute)

        nights_recomputed = len(affected_nights) if not dry_run else 0
        return inserted_total, skipped_total, nights_recomputed


async def _count_new_vs_dup(
    chunk: list[RawHealthRecord],
    profile_id: int,
) -> tuple[int, int]:
    """Count how many records in chunk are new vs already present in the DB.

    Uses COALESCE expressions matching the dedup index so NULL value_text/value_num
    records compare correctly.
    """
    new = 0
    dup = 0
    async with session_scope() as db:
        for s in chunk:
            clauses = _check_sample_exists_clause(profile_id, s)
            result = await db.execute(
                select(func.count()).select_from(HealthSample).where(*clauses)
            )
            if (result.scalar() or 0) > 0:
                dup += 1
            else:
                new += 1
    return new, dup
