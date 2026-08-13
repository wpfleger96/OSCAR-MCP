"""Health command group — Apple Health import and summaries."""

from __future__ import annotations

import asyncio
import re

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import (
    actor_options,
    date_range_options,
    db_option,
    init_db,
)
from snore.cli.decorators import (
    db_session as open_db_session,
)
from snore.cli.display import (
    ICON_CHART,
    ICON_SCAN,
    ICON_STATS,
    console,
    print_dry_run_complete,
    print_dry_run_header,
    print_footer,
    print_header,
    print_info,
    print_skip,
    print_success,
    print_table,
    print_warning,
)


def _fmt_hours(secs: float | None) -> str:
    if secs is None:
        return "—"
    return f"{secs / 3600:.1f}h"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct:.0f}%"


def _fmt_duration_label(start: datetime, end: datetime) -> str:
    delta_minutes = int((end - start).total_seconds() / 60)
    if delta_minutes < 60:
        return f"{delta_minutes}m"
    hours, mins = divmod(delta_minutes, 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


_STAGE_LABELS: dict[str, str] = {
    "InBed": "In Bed",
    "AsleepUnspecified": "Asleep (unspecified)",
    "Awake": "Awake",
    "AsleepCore": "Core",
    "AsleepDeep": "Deep",
    "AsleepREM": "REM",
}

_METRIC_LABELS: dict[str, str] = {
    "HKQuantityTypeIdentifierOxygenSaturation": "SpO2",
    "HKQuantityTypeIdentifierRespiratoryRate": "Resp. Rate",
    "HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances": "Breathing Disturbances",
    "HKQuantityTypeIdentifierHeartRate": "Heart Rate",
}

# Matches "HKQuantityTypeIdentifier" or "HKCategoryTypeIdentifier" prefix.
_HK_PREFIX = re.compile(r"^HK(?:Quantity|Category)TypeIdentifier")


def _stage_label(value_text: str | None) -> str:
    if value_text is None:
        return "—"
    return _STAGE_LABELS.get(value_text, value_text)


def _metric_label(record_type: str) -> str:
    if record_type in _METRIC_LABELS:
        return _METRIC_LABELS[record_type]
    return _HK_PREFIX.sub("", record_type)


@click.group()
def health() -> None:
    """Apple Health data commands."""
    pass


@health.command("import")
@click.argument("path", type=click.Path())
@click.option("--dry-run", is_flag=True, help="Preview without writing to the database")
@date_range_options
@click.option("--limit", "-n", type=int, default=None, help="Stop after N records")
@click.option(
    "--batch-size",
    type=int,
    default=500,
    show_default=True,
    help="Records per database transaction",
)
@db_option
@actor_options
def health_import(
    path: str,
    dry_run: bool,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int | None,
    batch_size: int,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """Import an Apple Health export (zip file or extracted directory)."""
    export_path = Path(path)
    if not export_path.exists():
        raise click.ClickException(f"Path does not exist: {export_path}")

    init_db(db)

    processed_ref: list[int] = [0]

    def _progress(total: int) -> None:
        processed_ref[0] = total
        console.print(
            f"\r{ICON_SCAN} Processing... {total} records", end="", highlight=False
        )

    async def _run() -> None:
        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415
        from snore.services.health_import_service import (
            HealthImportService,  # noqa: PLC0415
        )

        async with session_scope() as session:
            profile_id = await resolve_cli_profile_id(
                session, actor_user, actor_profile
            )

        if dry_run:
            print_dry_run_header("imported")

        try:
            result = await HealthImportService().import_file(
                export_path,
                profile_id,
                date_from=date_from.date() if date_from else None,
                date_to=date_to.date() if date_to else None,
                limit=limit,
                batch_size=batch_size,
                dry_run=dry_run,
                progress_callback=_progress,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e

        if processed_ref[0] > 0:
            console.print()  # end the carriage-return progress line

        print_header("Import Summary", ICON_STATS)
        if dry_run:
            print_info(f"Would insert:     {result.inserted} new records")
            print_skip(f"Already present: {result.skipped} records")
        else:
            print_success(f"Inserted:        {result.inserted} new records")
            print_skip(f"Skipped:         {result.skipped} duplicate records")
            print_info(f"Nights updated:  {result.nights_recomputed}")

        if result.unknown_metrics:
            total_unknown = sum(result.unknown_metrics.values())
            top = sorted(result.unknown_metrics.items(), key=lambda kv: -kv[1])[:3]
            top_str = ", ".join(f"{k}: {v}" for k, v in top)
            suffix = "..." if len(result.unknown_metrics) > 3 else ""
            print_warning(
                f"Unhandled types: {total_unknown} records ({top_str}{suffix})"
            )

        print_footer()

        if dry_run:
            print_dry_run_complete("import")

    asyncio.run(_run())


@health.command("list")
@date_range_options
@click.option(
    "--limit",
    "-n",
    type=int,
    default=30,
    show_default=True,
    help="Max nights to show",
)
@db_option
@actor_options
def health_list(
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """List nightly sleep summaries (newest first)."""

    async def _run() -> None:
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.database.models import HealthNightlySummary  # noqa: PLC0415

        async with open_db_session(db) as session:
            profile_id = await resolve_cli_profile_id(
                session, actor_user, actor_profile
            )

            stmt = sa_select(HealthNightlySummary).where(
                HealthNightlySummary.profile_id == profile_id
            )
            if date_from:
                stmt = stmt.where(HealthNightlySummary.night_date >= date_from.date())
            if date_to:
                stmt = stmt.where(HealthNightlySummary.night_date <= date_to.date())
            stmt = stmt.order_by(HealthNightlySummary.night_date.desc()).limit(limit)
            rows = list((await session.execute(stmt)).scalars().all())

        if not rows:
            console.print("No sleep data found")
            return

        print_table(
            [
                ("Date", 12),
                ("Sleep", 8),
                ("Eff%", 6),
                ("Core", 7),
                ("Deep", 7),
                ("REM", 7),
                ("Source", 0),
            ],
            (
                (
                    str(row.night_date),
                    _fmt_hours(row.total_sleep_seconds),
                    _fmt_pct(row.sleep_efficiency_pct),
                    _fmt_hours(row.core_seconds),
                    _fmt_hours(row.deep_seconds),
                    _fmt_hours(row.rem_seconds),
                    row.preferred_source or "—",
                )
                for row in rows
            ),
            wide=False,
        )

    asyncio.run(_run())


@health.command("show")
@click.argument("night_date", metavar="DATE", type=click.DateTime(formats=["%Y-%m-%d"]))
@db_option
@actor_options
def health_show(
    night_date: datetime,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """Show sleep detail for a single night (YYYY-MM-DD)."""
    night = night_date.date()

    async def _run() -> None:
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.database.models import (  # noqa: PLC0415
            HealthNightlySummary,
            HealthSample,
        )

        async with open_db_session(db) as session:
            profile_id = await resolve_cli_profile_id(
                session, actor_user, actor_profile
            )

            summary = (
                (
                    await session.execute(
                        sa_select(HealthNightlySummary).where(
                            HealthNightlySummary.profile_id == profile_id,
                            HealthNightlySummary.night_date == night,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if summary is None:
                raise click.ClickException(f"No health data for {night}")

            preferred_source = summary.preferred_source

            sleep_samples = list(
                (
                    await session.execute(
                        sa_select(HealthSample)
                        .where(
                            HealthSample.profile_id == profile_id,
                            HealthSample.night_date == night,
                            HealthSample.record_type
                            == "HKCategoryTypeIdentifierSleepAnalysis",
                            HealthSample.source_name == preferred_source,
                        )
                        .order_by(HealthSample.start_time)
                    )
                )
                .scalars()
                .all()
            )

            quantity_samples = list(
                (
                    await session.execute(
                        sa_select(HealthSample)
                        .where(
                            HealthSample.profile_id == profile_id,
                            HealthSample.night_date == night,
                            HealthSample.record_type
                            != "HKCategoryTypeIdentifierSleepAnalysis",
                        )
                        .order_by(HealthSample.start_time)
                    )
                )
                .scalars()
                .all()
            )

        # Display sleep stage intervals from the preferred source.
        if sleep_samples:
            source_display = preferred_source or "unknown"
            print_header(f"Sleep Intervals — {source_display}", ICON_SCAN)
            print_table(
                [("Start", 8), ("End", 8), ("Stage", 22), ("Duration", 0)],
                (
                    (
                        f"{s.start_time:%H:%M}",
                        f"{s.end_time:%H:%M}",
                        _stage_label(s.value_text),
                        _fmt_duration_label(s.start_time, s.end_time),
                    )
                    for s in sleep_samples
                ),
                wide=False,
            )

        # Summary totals block.
        print_header("Totals", ICON_STATS)
        for label, value in [
            ("Total sleep", _fmt_hours(summary.total_sleep_seconds)),
            ("Time in bed", _fmt_hours(summary.time_in_bed_seconds)),
            ("Efficiency", _fmt_pct(summary.sleep_efficiency_pct)),
            ("Core", _fmt_hours(summary.core_seconds)),
            ("Deep", _fmt_hours(summary.deep_seconds)),
            ("REM", _fmt_hours(summary.rem_seconds)),
            ("Awake", _fmt_hours(summary.awake_seconds)),
            ("Stage coverage", _fmt_pct(summary.stage_coverage_pct)),
        ]:
            console.print(f"  {label:<18} {value}", markup=False, highlight=False)
        print_footer()

        # Quantity samples (SpO2, heart rate, etc.) for that night.
        if quantity_samples:
            print_header("Health Samples", ICON_CHART)
            print_table(
                [("Type", 30), ("Value", 15), ("Timestamp", 0)],
                (
                    (
                        _metric_label(s.record_type),
                        f"{s.value_num} {s.unit}"
                        if s.value_num is not None
                        else str(s.value_text or ""),
                        f"{s.start_time:%Y-%m-%d %H:%M}",
                    )
                    for s in quantity_samples
                ),
                wide=False,
            )

    asyncio.run(_run())
