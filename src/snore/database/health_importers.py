"""Database importers for Apple Health samples and nightly sleep summaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import HealthNightlySummary, HealthSample, utc_now
from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.type_handlers import SLEEP_TYPE

__all__ = ["HealthSampleImporter"]

# Stage sets for summary computation.
_STAGE_SLEEP = frozenset({"AsleepCore", "AsleepDeep", "AsleepREM"})
_TOTAL_SLEEP_STAGES = frozenset(
    {"AsleepCore", "AsleepDeep", "AsleepREM", "AsleepUnspecified"}
)


class HealthSampleImporter:
    """Bulk-insert Apple Health samples and recompute nightly sleep summaries."""

    async def insert_samples_batch(
        self,
        samples: Sequence[RawHealthRecord],
        profile_id: int,
        db: AsyncSession,
    ) -> tuple[int, int, set[date]]:
        """Insert a batch of records, skipping duplicates via the expression-index dedup.

        Returns (inserted, skipped_duplicates, affected_night_dates).
        affected_night_dates conservatively includes every night in the batch —
        recompute is idempotent so over-recomputing an unaffected night is safe.
        """
        if not samples:
            return 0, 0, set()

        now = utc_now()
        rows = [
            {
                "profile_id": profile_id,
                "record_type": s.record_type,
                "source_name": s.source_name,
                "source_version": s.source_version,
                "device_info": s.device_info,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "value_text": s.value_text,
                "value_num": s.value_num,
                "unit": s.unit,
                "night_date": s.night_date,
                "utc_offset_seconds": s.utc_offset_seconds,
                "ingest_channel": s.ingest_channel,
                "imported_at": now,
            }
            for s in samples
        ]

        # INSERT OR IGNORE — no index_elements because the dedup constraint is an
        # expression index (COALESCE sentinels). A column-list conflict target would
        # not match it; the catch-all form maps to INSERT OR IGNORE in SQLite and
        # ON CONFLICT DO NOTHING in PostgreSQL, both of which work without a target.
        stmt = insert(HealthSample).on_conflict_do_nothing().values(rows)
        result = await db.execute(stmt)

        # rowcount from a single multi-VALUES INSERT OR IGNORE reflects
        # SQLite's changes() count = rows actually inserted (not ignored).
        # Per tests (see test_health_importers.py), this is reliable under aiosqlite
        # for multi-row inserts because SQLAlchemy emits one cursor.execute() call
        # (not executemany), so sqlite3_changes() is accurate.
        rowcount: int = getattr(result, "rowcount", -1)
        inserted = rowcount if rowcount >= 0 else 0
        skipped = len(samples) - inserted
        affected = {s.night_date for s in samples}
        return inserted, skipped, affected

    async def recompute_nightly_summary(
        self,
        profile_id: int,
        night_date: date,
        db: AsyncSession,
    ) -> None:
        """Delete-and-recompute the nightly sleep summary for one profile+night.

        If no sleep samples exist for this night, removes the summary row if present.
        """
        result = await db.execute(
            select(HealthSample).where(
                HealthSample.profile_id == profile_id,
                HealthSample.record_type == SLEEP_TYPE,
                HealthSample.night_date == night_date,
            )
        )
        samples = list(result.scalars().all())

        if not samples:
            await db.execute(
                delete(HealthNightlySummary).where(
                    HealthNightlySummary.profile_id == profile_id,
                    HealthNightlySummary.night_date == night_date,
                )
            )
            return

        # Group by source and choose the preferred one.
        by_source: dict[str, list[HealthSample]] = defaultdict(list)
        for s in samples:
            by_source[s.source_name].append(s)

        preferred = _pick_preferred_source(by_source)

        # Sum interval durations per canonical stage value from the preferred source.
        stage_secs: dict[str, float] = {}
        for s in by_source[preferred]:
            dur = (s.end_time - s.start_time).total_seconds()
            val = s.value_text or ""
            stage_secs[val] = stage_secs.get(val, 0.0) + dur

        total_sleep = sum(stage_secs.get(v, 0.0) for v in _TOTAL_SLEEP_STAGES)
        time_in_bed = stage_secs.get("InBed", 0.0)
        awake = stage_secs.get("Awake", 0.0)
        core = stage_secs.get("AsleepCore", 0.0)
        deep = stage_secs.get("AsleepDeep", 0.0)
        rem = stage_secs.get("AsleepREM", 0.0)
        unspecified = stage_secs.get("AsleepUnspecified", 0.0)

        # None only when the denominator is zero — undefined, not zero efficiency.
        sleep_efficiency = (
            (total_sleep / time_in_bed * 100) if time_in_bed > 0 else None
        )
        stage_coverage = (
            ((core + deep + rem) / total_sleep * 100) if total_sleep > 0 else None
        )

        now = utc_now()

        # Simple select-then-insert-or-update; runs inside the caller's transaction.
        existing = (
            (
                await db.execute(
                    select(HealthNightlySummary).where(
                        HealthNightlySummary.profile_id == profile_id,
                        HealthNightlySummary.night_date == night_date,
                    )
                )
            )
            .scalars()
            .first()
        )

        if existing is not None:
            existing.preferred_source = preferred
            existing.time_in_bed_seconds = time_in_bed
            existing.total_sleep_seconds = total_sleep
            existing.core_seconds = core
            existing.deep_seconds = deep
            existing.rem_seconds = rem
            existing.awake_seconds = awake
            existing.unspecified_seconds = unspecified
            existing.sleep_efficiency_pct = sleep_efficiency
            existing.stage_coverage_pct = stage_coverage
            existing.computed_at = now
        else:
            db.add(
                HealthNightlySummary(
                    profile_id=profile_id,
                    night_date=night_date,
                    preferred_source=preferred,
                    time_in_bed_seconds=time_in_bed,
                    total_sleep_seconds=total_sleep,
                    core_seconds=core,
                    deep_seconds=deep,
                    rem_seconds=rem,
                    awake_seconds=awake,
                    unspecified_seconds=unspecified,
                    sleep_efficiency_pct=sleep_efficiency,
                    stage_coverage_pct=stage_coverage,
                    computed_at=now,
                )
            )


def _pick_preferred_source(by_source: dict[str, list[HealthSample]]) -> str:
    """Choose the preferred source for nightly summary computation.

    Priority:
    1. Source whose lowercased name contains "watch" AND has stage records
       (AsleepCore / AsleepDeep / AsleepREM).
    2. Any source with records.

    Tie-break within each group: most total sleep seconds (TOTAL_SLEEP_STAGES),
    then lexicographic source name (ascending).
    """

    def _total_sleep_secs(records: list[HealthSample]) -> float:
        return sum(
            (r.end_time - r.start_time).total_seconds()
            for r in records
            if (r.value_text or "") in _TOTAL_SLEEP_STAGES
        )

    def _has_stage(records: list[HealthSample]) -> bool:
        return any((r.value_text or "") in _STAGE_SLEEP for r in records)

    watch = sorted(
        [
            n
            for n, recs in by_source.items()
            if "watch" in n.lower() and _has_stage(recs)
        ],
        key=lambda n: (-_total_sleep_secs(by_source[n]), n),
    )
    if watch:
        return watch[0]

    return sorted(
        list(by_source),
        key=lambda n: (-_total_sleep_secs(by_source[n]), n),
    )[0]


def _check_sample_exists_clause(
    profile_id: int,
    s: RawHealthRecord,
) -> list[Any]:
    """Build where-clause elements for a dry-run existence check.

    Uses COALESCE expressions mirroring the uq_health_sample_dedup expression
    index so that NULL value_text / value_num records match correctly.
    """
    return [
        HealthSample.profile_id == profile_id,
        HealthSample.record_type == s.record_type,
        HealthSample.source_name == s.source_name,
        HealthSample.start_time == s.start_time,
        HealthSample.end_time == s.end_time,
        text("coalesce(health_samples.value_text, '') = coalesce(:vt, '')").bindparams(
            vt=s.value_text
        ),
        text(
            "coalesce(health_samples.value_num, -1.0) = coalesce(:vn, -1.0)"
        ).bindparams(vn=s.value_num),
    ]
