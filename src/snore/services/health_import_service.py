"""HealthImportService — orchestrates the Apple Health export.xml ingestion pipeline."""

from __future__ import annotations

import itertools
import logging

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.health_importers import HealthSampleImporter
from snore.database.session import session_scope
from snore.database.txn import run_txn
from snore.database.write_gate import write_gate
from snore.parsers.apple_health import xml_reader
from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.parser import AppleHealthParser
from snore.parsers.apple_health.type_handlers import SLEEP_TYPE
from snore.services.schemas import HealthImportResult

logger = logging.getLogger(__name__)

__all__ = ["HealthImportService"]

# Number of nights per recompute transaction. Batching avoids O(N) separate
# write_gate acquisitions for multi-year backfills (e.g. 1 095 nights = 3 years).
_RECOMPUTE_BATCH_SIZE = 50


class _DryRunRollback(BaseException):
    """Sentinel raised inside a dry-run transaction to force rollback after rowcount capture.

    BaseException (not Exception) ensures session_scope's ``except Exception``
    clause does not swallow it; the session.begin() context manager still rolls
    back on any BaseException, and the finally block still closes the session.
    """


class HealthImportService:
    """Orchestrates the Apple Health import pipeline for export.xml files."""

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
        cancel_predicate: Callable[[], bool] | None = None,
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
            cancel_predicate: When provided, called at the start of each batch. If
                it returns True, record consumption stops immediately. Batches already
                committed are kept; nightly summaries for all committed nights are still
                recomputed so the DB remains consistent. Partial counts are returned.
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
            records_iter,
            profile_id,
            batch_size,
            dry_run,
            progress_callback,
            cancel_predicate,
        )

        return HealthImportResult(
            inserted=inserted,
            skipped=skipped,
            unknown_metrics=skip_counter,
            nights_recomputed=nights_recomputed,
            dry_run=dry_run,
        )

    async def _import_records(
        self,
        records_iter: Iterator[RawHealthRecord],
        profile_id: int,
        batch_size: int,
        dry_run: bool,
        progress_callback: Callable[[int], None] | None,
        cancel_predicate: Callable[[], bool] | None = None,
    ) -> tuple[int, int, int]:
        """Consume records_iter in batches; persist or dry-run count each batch.

        Returns (inserted, skipped, nights_recomputed).

        nights_recomputed counts only nights that received SLEEP records —
        recomputing a quantity-only night is a no-op and excluded from the count.

        When *cancel_predicate* is provided it is called at the start of each
        batch iteration. If it returns True, record consumption stops and no
        further batches are written. Batches already committed to the DB are
        kept; nightly summaries for all committed nights are still recomputed
        so the DB remains consistent. Partial counts are returned.
        """
        inserted_total = 0
        skipped_total = 0
        affected_nights: set[date] = set()
        processed = 0

        importer = HealthSampleImporter()

        for _chunk_num in itertools.count(1):
            if cancel_predicate is not None and cancel_predicate():
                break
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
                ) -> tuple[int, int]:
                    ci, cs, _ = await _i.insert_samples_batch(_c, profile_id, db)
                    return ci, cs

                async with write_gate():
                    ci, cs = await run_txn(_insert)
                inserted_total += ci
                skipped_total += cs
                # Only track nights that received SLEEP records: recomputing a
                # quantity-only night (SpO2, RR) is a no-op and inflates the count.
                affected_nights.update(
                    s.night_date for s in chunk if s.record_type == SLEEP_TYPE
                )

            if progress_callback is not None:
                progress_callback(processed)

        if not dry_run and affected_nights:
            sorted_nights = sorted(affected_nights)
            for slice_start in range(0, len(sorted_nights), _RECOMPUTE_BATCH_SIZE):
                night_slice = sorted_nights[
                    slice_start : slice_start + _RECOMPUTE_BATCH_SIZE
                ]

                async def _recompute(
                    db: AsyncSession,
                    *,
                    _nights: list[date] = night_slice,
                    _i: HealthSampleImporter = importer,
                ) -> None:
                    for n in _nights:
                        await _i.recompute_nightly_summary(profile_id, n, db)

                async with write_gate():
                    await run_txn(_recompute)

        nights_recomputed = len(affected_nights) if not dry_run else 0
        return inserted_total, skipped_total, nights_recomputed


async def _count_new_vs_dup(
    chunk: list[RawHealthRecord],
    profile_id: int,
) -> tuple[int, int]:
    """Count new vs duplicate records using a rolled-back INSERT OR IGNORE.

    Runs the same bulk INSERT OR IGNORE as the real import path inside a
    ``BEGIN IMMEDIATE`` transaction that is always rolled back, deriving
    new-vs-dup from the SQLite changes() rowcount exactly as the live path does.
    Dedup semantics are guaranteed identical — including COALESCE NULL-sentinel
    handling in the expression index — with no duplicated logic.
    """
    inserted = 0
    try:
        async with session_scope(immediate=True) as db:
            inserted, _, _ = await HealthSampleImporter().insert_samples_batch(
                chunk, profile_id, db
            )
            raise _DryRunRollback()
    except _DryRunRollback:
        pass
    return inserted, len(chunk) - inserted
