"""
Command-line interface for SNORE.

Provides commands for importing CPAP data, querying sessions, and database management.
"""

import logging
import sys

from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from snore.analysis.modes.types import ModeResult
    from snore.analysis.service import AnalysisResult
    from snore.analysis.types import AnalysisEvent

import click

from snore.constants import (
    DEFAULT_LIST_SESSIONS_LIMIT,
    abbreviate_event_type,
)
from snore.logging_config import setup_logging
from snore.parsers.register_all import register_all_parsers
from snore.parsers.registry import parser_registry
from snore.waveform import format_time_offset
from snore.waveform.inspector import parse_time_offset

logger = logging.getLogger(__name__)

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


def version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Show version and check for updates."""
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"snore, version {__version__}")

    try:
        from snore.bootstrap import check_tool_updates

        update_info = check_tool_updates(timeout=3)
        if update_info and update_info.has_update:
            click.echo(
                f"\nUpdate available: {update_info.current_version} → {update_info.latest_version}"
            )
            click.echo("Run 'snore upgrade' to install")
    except Exception as e:
        logger.debug(f"Failed to check for updates: {e}")

    ctx.exit()


@click.group()
@click.option(
    "--version",
    is_flag=True,
    callback=version_callback,
    expose_value=False,
    is_eager=True,
    help="Show version and check for updates",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose: bool) -> None:
    """SNORE: CPAP Data Management Tool"""
    setup_logging(verbose=verbose, console_format="%(levelname)s: %(message)s")


@cli.command()
@click.option("--github", is_flag=True, help="Install from GitHub instead of PyPI")
@click.option("--force", is_flag=True, help="Force reinstall")
@click.option("--dry-run", is_flag=True, help="Show what would be done")
@click.option("--skip-completions", is_flag=True, help="Skip shell completion setup")
def setup(github: bool, force: bool, dry_run: bool, skip_completions: bool) -> None:
    """Install SNORE globally as a uv tool."""
    from snore.bootstrap import install_tool

    source_name = "GitHub" if github else "PyPI"
    click.echo(f"Installing SNORE from {source_name}...")

    success, message = install_tool(from_github=github, force=force, dry_run=dry_run)

    if success:
        click.echo(f"✓ {message}")
        click.echo("\nYou can now run 'snore' from anywhere!")

        if not skip_completions:
            from snore.completions import detect_shell, install_completion

            shell = detect_shell()
            if shell:
                comp_success, comp_msg = install_completion(shell, dry_run=dry_run)
                if comp_success:
                    click.echo(f"✓ Shell completions: {comp_msg}")
                else:
                    click.echo(f"⚠ Shell completions: {comp_msg}", err=True)
            else:
                click.echo("⚠ Could not detect shell for completion setup")
                click.echo(
                    "  Run 'snore completions install --shell <bash|zsh>' manually"
                )
    else:
        click.echo(f"✗ {message}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.option("--force", is_flag=True, help="Force reinstall")
def upgrade(check: bool, force: bool) -> None:
    """Upgrade SNORE to the latest version."""
    from snore.bootstrap import check_tool_updates, get_tool_source, perform_update

    source = get_tool_source("snore")
    source_name = {"github": "GitHub", "pypi": "PyPI", "local": "local"}.get(
        source or "", "PyPI"
    )

    click.echo(f"Checking for updates from {source_name}...")

    update_info = check_tool_updates()

    if not update_info:
        click.echo("✗ Could not check for updates", err=True)
        sys.exit(1)

    if update_info.has_update:
        click.echo(
            f"Update available: {update_info.current_version} → {update_info.latest_version}"
        )

        if check:
            click.echo("\nRun 'snore upgrade' to install")
            return

        if not force:
            if not click.confirm("Install update?", default=True):
                click.echo("Cancelled")
                return

        click.echo("Upgrading...")
        success, message, was_upgraded = perform_update(force=force)

        if success:
            if was_upgraded:
                click.echo(f"✓ {message}")
            else:
                click.echo("✓ Already up to date")
        else:
            click.echo(f"✗ {message}", err=True)
            sys.exit(1)
    else:
        click.echo("✓ Already up to date")


@cli.command("import")
@click.argument("path", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Re-import existing sessions")
@click.option(
    "--db", type=click.Path(), help="Database path (default: ~/snore/snore.db)"
)
@click.option("--limit", "-n", type=int, help="Limit to first N sessions")
@click.option(
    "--sort-by",
    type=click.Choice(["date-asc", "date-desc", "filesystem"]),
    default="filesystem",
    help="Session sort order (default: filesystem)",
)
@click.option(
    "--from",
    "date_from",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Import sessions from this date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "date_to",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Import sessions up to this date (YYYY-MM-DD)",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be imported without importing"
)
@click.option(
    "--no-parallel", is_flag=True, help="Disable parallel parsing (for debugging)"
)
@click.option(
    "--batch-size",
    type=int,
    default=50,
    help="Number of sessions per database transaction (default: 50)",
)
@click.option(
    "--all",
    "select_all",
    is_flag=True,
    help="Import all detected data sources without prompting",
)
def import_data(
    path: str,
    force: bool,
    db: str | None,
    limit: int | None,
    sort_by: str,
    date_from: datetime | None,
    date_to: datetime | None,
    dry_run: bool,
    no_parallel: bool,
    batch_size: int,
    select_all: bool,
) -> int:
    """Import CPAP data from device SD card or directory."""
    from snore.database.importers import SessionImporter
    from snore.database.session import init_database, session_scope

    data_path = Path(path)

    register_all_parsers()

    click.echo(f"📂 Scanning {data_path}...")
    results = parser_registry.detect_all_parsers(data_path)

    if not results:
        click.echo("❌ Error: No compatible parser found for this data", err=True)
        click.echo("\nSupported devices:")
        for p in parser_registry.list_parsers():
            click.echo(f"  - {p.manufacturer}: {p.parser_id}")
        return 1

    expanded_sources = []
    for parser, detection in results:
        meta = detection.metadata or {}
        all_roots = meta.get("all_roots", [])

        if not all_roots:
            expanded_sources.append(
                {
                    "parser": parser,
                    "detection": detection,
                    "root_path": meta.get("data_root"),
                    "profile_name": meta.get("profile_name"),
                    "structure_type": meta.get("structure_type"),
                    "device_serial": meta.get("device_serial"),
                }
            )
        else:
            root_metadata = meta.get("root_metadata", {})
            for root_path in all_roots:
                root_info = root_metadata.get(root_path, {})
                expanded_sources.append(
                    {
                        "parser": parser,
                        "detection": detection,
                        "root_path": root_path,
                        "profile_name": root_info.get(
                            "profile_name", meta.get("profile_name")
                        ),
                        "structure_type": root_info.get(
                            "structure_type", meta.get("structure_type")
                        ),
                        "device_serial": root_info.get(
                            "device_serial", meta.get("device_serial")
                        ),
                    }
                )

    selected_sources = []
    if len(expanded_sources) > 1:
        click.echo(f"\nFound {len(expanded_sources)} data sources:\n")
        for i, source in enumerate(expanded_sources, 1):
            profile = source.get("profile_name") or "unknown"
            structure_type = source.get("structure_type")
            structure = str(structure_type or "unknown").replace("_", " ")
            parser_obj = source.get("parser")
            parser_name = parser_obj.manufacturer if parser_obj else "unknown"  # type: ignore[union-attr]

            click.echo(f"  {i}. {parser_name} - {profile} ({structure})")
            root = source.get("root_path")
            if root:
                click.echo(f"     Path: {root}")

        if select_all:
            selected_sources = expanded_sources
        else:
            selection = click.prompt(
                "\nSelect sources to import (comma-separated numbers, or 'all')",
                default="all",
            )

            if selection.lower() == "all":
                selected_sources = expanded_sources
            else:
                try:
                    indices = [int(x.strip()) - 1 for x in selection.split(",")]
                    selected_sources = [
                        expanded_sources[i]
                        for i in indices
                        if 0 <= i < len(expanded_sources)
                    ]
                    if not selected_sources:
                        click.echo("❌ Invalid selection: no valid indices", err=True)
                        return 1
                except (ValueError, IndexError):
                    click.echo(f"❌ Invalid selection: {selection}", err=True)
                    return 1
    else:
        selected_sources = expanded_sources

    init_database(str(Path(db)) if db else None)

    with session_scope() as session:
        orphaned_count = SessionImporter.cleanup_orphaned_records(session)
        if orphaned_count > 0:
            click.echo(f"⚠️  Cleaned up {orphaned_count} orphaned records from database")

    total_imported = 0
    total_skipped = 0
    total_failed = 0

    for source in selected_sources:
        parser = source.get("parser")  # type: ignore[assignment]
        if not parser:
            continue
        source_desc = (
            source.get("profile_name")
            or f"S/N {source.get('device_serial', 'unknown')}"
        )

        if len(selected_sources) > 1:
            click.echo(f"\n{'=' * 60}")
            click.echo(f"Processing: {source_desc}")
            click.echo(f"{'=' * 60}")

        click.echo(f"✓ Detected: {parser.manufacturer} ({parser.parser_id})")
        structure_val = source.get("structure_type")
        click.echo(f"  Structure: {str(structure_val or 'unknown').replace('_', ' ')}")
        root_val = source.get("root_path")
        if root_val:
            click.echo(f"  Data root: {root_val}")

        date_from_str = date_from.strftime("%Y-%m-%d") if date_from else None
        date_to_str = date_to.strftime("%Y-%m-%d") if date_to else None

        if limit or date_from or date_to or sort_by != "filesystem":
            click.echo("\n📋 Import filters:")
            if limit:
                click.echo(f"  • Limit: {limit} sessions")
            if sort_by != "filesystem":
                order_desc = "oldest first" if sort_by == "date-asc" else "newest first"
                click.echo(f"  • Sort: {order_desc}")
            if date_from:
                click.echo(f"  • From: {date_from:%Y-%m-%d}")
            if date_to:
                click.echo(f"  • To: {date_to:%Y-%m-%d}")

        click.echo("\n📋 Parsing sessions...")
        try:
            root_path = source.get("root_path")
            sessions = list(
                parser.parse_sessions(
                    Path(str(root_path)) if root_path else data_path,
                    date_from=date_from_str,
                    date_to=date_to_str,
                    limit=limit,
                    sort_by=sort_by if sort_by != "filesystem" else None,
                    parallel=not no_parallel,
                )
            )
        except Exception as e:
            click.echo(f"❌ Error parsing sessions: {e}", err=True)
            if logging.getLogger().level == logging.DEBUG:
                raise
            if len(selected_sources) > 1:
                continue
            return 1

        if not sessions:
            click.echo("⚠️  No sessions found")
            if len(selected_sources) > 1:
                continue
            return 0

        click.echo(f"✓ Found {len(sessions)} sessions")

        if dry_run:
            click.echo("\n🔍 DRY RUN MODE - No data will be imported\n")
            click.echo(
                f"{'Date':<12} {'Time':<8} {'Duration':<10} {'AHI':<6} {'Events':<8}"
            )
            click.echo("=" * 55)

            total_duration = 0.0
            total_events = 0

            sorted_sessions = sorted(sessions, key=lambda s: s.start_time, reverse=True)

            for unified_session in sorted_sessions:
                duration_hours = (
                    unified_session.duration_seconds / 3600
                    if unified_session.duration_seconds
                    else 0
                )
                total_duration += duration_hours

                num_events = (
                    len(unified_session.events) if unified_session.events else 0
                )
                total_events += num_events

                ahi_str = "N/A"
                if (
                    hasattr(unified_session, "statistics")
                    and unified_session.statistics
                ):
                    if unified_session.statistics.ahi is not None:
                        ahi_str = f"{unified_session.statistics.ahi:.1f}"

                click.echo(
                    f"{unified_session.start_time:%Y-%m-%d}   {unified_session.start_time:%H:%M:%S}  "
                    f"{duration_hours:>6.1f}h    "
                    f"{ahi_str:>5}  "
                    f"{num_events:>6}"
                )

            click.echo("=" * 55)
            click.echo("\n📊 Summary:")
            click.echo(f"  • Total sessions: {len(sessions)}")
            click.echo(f"  • Total duration: {total_duration:.1f} hours")
            click.echo(f"  • Total events: {total_events}")
            if sessions:
                first_date = min(s.start_time for s in sessions)
                last_date = max(s.start_time for s in sessions)
                click.echo(
                    f"  • Date range: {first_date:%Y-%m-%d} to {last_date:%Y-%m-%d}"
                )
            if len(selected_sources) == 1:
                click.echo("\n✓ Dry run complete. Use without --dry-run to import.")
            continue

        importer = SessionImporter()

        total_batches = (len(sessions) + batch_size - 1) // batch_size
        click.echo(
            f"📥 Importing {len(sessions)} sessions in {total_batches} batch(es)..."
        )

        imported, skipped, failed = importer.import_sessions_batch(
            sessions, force=force, batch_size=batch_size
        )

        total_imported += imported
        total_skipped += skipped
        total_failed += failed

        if len(selected_sources) > 1:
            click.echo(f"\n{'=' * 50}")
            click.echo(f"📊 Summary for {source_desc}")
            click.echo(f"{'=' * 50}")
            click.echo(f"✓ Imported: {imported} sessions")
            if skipped > 0:
                click.echo(f"⊝ Skipped:  {skipped} sessions")
            if failed > 0:
                click.echo(f"❌ Failed:   {failed} sessions")

    if dry_run and len(selected_sources) > 1:
        click.echo(f"\n{'=' * 50}")
        click.echo("📊 Overall Dry Run Summary")
        click.echo(f"{'=' * 50}")
        click.echo(f"✓ Total data sources: {len(selected_sources)}")
        click.echo("\n✓ Dry run complete. Use without --dry-run to import.")
        return 0
    elif dry_run:
        return 0

    click.echo(f"\n{'=' * 50}")
    click.echo("📊 Overall Import Summary")
    click.echo(f"{'=' * 50}")
    click.echo(f"✓ Imported: {total_imported} sessions")
    if total_skipped > 0:
        click.echo(
            f"⊝ Skipped:  {total_skipped} sessions (already exist, use --force to re-import)"
        )
    if total_failed > 0:
        click.echo(f"❌ Failed:   {total_failed} sessions")

    click.echo(f"{'=' * 50}")

    if total_failed > 0:
        return 1
    return 0


@cli.command()
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--days", type=int, help="Limit to last N days")
@click.option(
    "--period",
    type=click.Choice(["week", "month", "6month", "year"]),
    help="Show statistics broken down by period",
)
@click.option("--trend", is_flag=True, help="Show trend analysis chart")
@click.option(
    "--records", is_flag=True, help="Show top 5 best/worst days for key metrics"
)
def stats(
    db: str | None, days: int | None, period: str | None, trend: bool, records: bool
) -> None:
    """Show therapy usage and clinical statistics."""
    from snore.database.session import init_database, session_scope
    from snore.services.schemas import PeriodStatistics
    from snore.services.stats_service import StatsService

    if trend and not period:
        period = "week"

    init_database(str(Path(db)) if db else None)

    with session_scope() as session:
        service = StatsService(session)
        summary = service.get_summary(days)

        if not summary:
            click.echo("\n📈 Therapy Statistics")
            click.echo(f"{'=' * 50}")
            click.echo("\nNo therapy data found.")
            click.echo(f"{'=' * 50}\n")
            return

        click.echo("\n📈 Therapy Statistics")
        click.echo(f"{'=' * 50}")

        click.echo("\nDate Range")
        click.echo(f"  First session: {summary.first_date}")
        click.echo(f"  Last session: {summary.last_date}")
        click.echo(f"  Days since last use: {summary.days_since_last}")

        click.echo("\nUsage")
        click.echo(f"  Total therapy hours: {summary.total_hours:,.1f} hrs")
        click.echo(f"  Average per night: {summary.avg_hours:.1f} hrs")
        click.echo(f"  Days with data: {summary.days_with_data}")

        click.echo("\nClinical")
        if summary.avg_ahi is not None:
            click.echo(f"  Average AHI: {summary.avg_ahi:.1f}")
        else:
            click.echo("  Average AHI: N/A")
        click.echo(f"  Effectiveness: {summary.effectiveness}")

        if summary.avg_rei is not None:
            click.echo(f"  Average REI: {summary.avg_rei:.1f}")

        if summary.avg_pressure is not None:
            click.echo("\nPressure")
            click.echo(f"  Average: {summary.avg_pressure:.1f} cmH₂O")
            if summary.min_pressure is not None and summary.max_pressure is not None:
                click.echo(
                    f"  Range: {summary.min_pressure:.1f} - {summary.max_pressure:.1f} cmH₂O"
                )

        if summary.avg_epap is not None:
            click.echo("\nEPAP")
            click.echo(f"  Average: {summary.avg_epap:.1f} cmH₂O")

        if summary.avg_leak is not None:
            click.echo("\nLeak")
            click.echo(f"  Average: {summary.avg_leak:.1f} L/min")
            leak_assessment = "well controlled" if summary.avg_leak < 24 else "elevated"
            click.echo(f"  Assessment: {leak_assessment}")

        if summary.avg_spo2 is not None:
            click.echo("\nSpO₂")
            click.echo(f"  Average: {summary.avg_spo2:.1f}%")
            if summary.min_spo2 is not None:
                click.echo(f"  Minimum recorded: {summary.min_spo2:.0f}%")

        if summary.total_spo2_time_below_90 > 0:
            minutes_below_90 = summary.total_spo2_time_below_90 / 60
            click.echo(f"  Time below 90%: {minutes_below_90:.1f} minutes")

        if summary.avg_pulse is not None:
            click.echo("\nPulse")
            click.echo(f"  Average: {summary.avg_pulse:.1f} BPM")

        if (
            summary.avg_respiratory_rate is not None
            or summary.avg_tidal_volume is not None
            or summary.avg_minute_ventilation is not None
        ):
            click.echo("\nRespiratory")
            if summary.avg_respiratory_rate is not None:
                click.echo(
                    f"  Respiratory Rate: {summary.avg_respiratory_rate:.1f} breaths/min"
                )
            if summary.avg_tidal_volume is not None:
                click.echo(f"  Tidal Volume: {summary.avg_tidal_volume:.0f} mL")
            if summary.avg_minute_ventilation is not None:
                click.echo(
                    f"  Minute Ventilation: {summary.avg_minute_ventilation:.1f} L/min"
                )

        if summary.event_counts:
            click.echo("\nEvents")
            for ec in summary.event_counts:
                click.echo(f"  {ec.event_type}: {ec.count:,} ({ec.percentage:.1f}%)")

        if period:
            period_literal = cast(Literal["week", "month", "6month", "year"], period)
            period_stats: list[PeriodStatistics] = service.get_period_statistics(
                period_literal, days
            )

            if period_stats:
                period_names = {
                    "week": "Weekly",
                    "month": "Monthly",
                    "6month": "6-Month",
                    "year": "Yearly",
                }

                click.echo(f"\n\nTherapy Statistics ({period_names[period]})")
                click.echo(f"{'=' * 80}")

                click.echo(
                    f"{'Period':<20} {'Days':<6} {'Avg Hours':<11} {'Avg AHI':<9} {'Med AHI':<9}"
                )
                click.echo("-" * 80)

                for period_stat in period_stats:  # type: PeriodStatistics
                    if period == "week":
                        period_label = f"{period_stat.period_start.strftime('%Y-W%U')}"
                    elif period == "month":
                        period_label = period_stat.period_start.strftime("%b %Y")
                    elif period == "6month":
                        half = "H1" if period_stat.period_start.month == 1 else "H2"
                        period_label = f"{period_stat.period_start.year} {half}"
                    else:
                        period_label = str(period_stat.period_start.year)

                    days_str = f"{period_stat.days_used}/{period_stat.days_in_period}"

                    hours_str = (
                        f"{period_stat.avg_hours_per_day:.1f}h"
                        if period_stat.avg_hours_per_day is not None
                        else "N/A"
                    )

                    avg_ahi_str = (
                        f"{period_stat.avg_ahi:.1f}"
                        if period_stat.avg_ahi is not None
                        else "N/A"
                    )

                    med_ahi_str = (
                        f"{period_stat.median_ahi:.1f}"
                        if period_stat.median_ahi is not None
                        else "N/A"
                    )

                    click.echo(
                        f"{period_label:<20} {days_str:<6} {hours_str:<11} {avg_ahi_str:<9} {med_ahi_str:<9}"
                    )

                click.echo("=" * 80)

                if trend:
                    import plotext as plt

                    trends = service.get_trends(period_stats)
                    ahi_trend = trends["ahi"]

                    ahi_values = [v for _, v in ahi_trend if v is not None]
                    if ahi_values:
                        dates_for_plot = [d for d, v in ahi_trend if v is not None]
                        date_labels = [d.strftime("%Y-%m-%d") for d in dates_for_plot]
                        x_indices = list(range(len(ahi_values)))

                        latest_ahi = ahi_values[-1]
                        if len(ahi_values) > 1:
                            prior_avg = sum(ahi_values[:-1]) / len(ahi_values[:-1])
                            if latest_ahi < prior_avg * 0.9:
                                direction = "(improving)"
                            elif latest_ahi > prior_avg * 1.1:
                                direction = "(worsening)"
                            else:
                                direction = "(stable)"
                        else:
                            direction = ""

                        click.echo("\n\nAHI Trend")
                        click.echo("=" * 80)

                        plt.clf()
                        plt.plot(x_indices, ahi_values, marker="braille")
                        plt.xticks(x_indices, date_labels)
                        plt.title(f"AHI Over Time {direction}")
                        plt.xlabel("Period")
                        plt.ylabel("AHI (events/hour)")
                        plt.show()

                        click.echo("=" * 80)

        if records:
            records_data = service.get_records(days, top_n=5)

            if records_data:
                click.echo("\n\nRecords (Top 5)")
                click.echo("=" * 80)

                metric_labels = {
                    "ahi": ("Best AHI", "Worst AHI"),
                    "leak": ("Best Leak", "Worst Leak"),
                    "therapy_hours": ("Longest Sessions", "Shortest Sessions"),
                    "spo2_min": ("Best SpO2 Min", "Worst SpO2 Min"),
                }

                for metric, (best_label, worst_label) in metric_labels.items():
                    if metric not in records_data:
                        continue

                    best_records = records_data[metric]["best"]
                    worst_records = records_data[metric]["worst"]

                    click.echo(f"\n{best_label:<35} {worst_label}")
                    click.echo("-" * 80)

                    max_rows = max(len(best_records), len(worst_records))
                    for i in range(max_rows):
                        best_str = ""
                        worst_str = ""

                        if i < len(best_records):
                            dt, val = best_records[i]
                            if metric == "therapy_hours":
                                best_str = f"  {dt}: {val:.1f}h"
                            else:
                                best_str = f"  {dt}: {val:.1f}"

                        if i < len(worst_records):
                            dt, val = worst_records[i]
                            if metric == "therapy_hours":
                                worst_str = f"{dt}: {val:.1f}h"
                            else:
                                worst_str = f"{dt}: {val:.1f}"

                        click.echo(f"{best_str:<35} {worst_str}")

                click.echo("=" * 80)

        click.echo(f"\n{'=' * 50}\n")


@cli.group()
def db() -> None:
    """Database management commands."""
    pass


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
def init(db: str | None) -> int | None:
    """Initialize database (creates tables if needed)."""
    from snore.constants import DEFAULT_DATABASE_PATH
    from snore.database.models import Base
    from snore.database.session import init_database

    if db:
        db_path = Path(db)
    else:
        db_path = Path(DEFAULT_DATABASE_PATH)

    db_existed = db_path.exists()

    init_database(str(db_path))

    table_names = sorted(Base.metadata.tables.keys())

    if db_existed:
        click.secho(
            f"✓ Database already initialized at {db_path}", fg="blue", bold=True
        )
        click.echo("\nVerified tables:")
    else:
        click.secho(f"✓ Created new database at {db_path}", fg="green", bold=True)
        click.echo("\nInitialized tables:")

    for table_name in table_names:
        click.echo(f"    - {table_name}")

    if db_existed:
        click.echo("\nNo changes needed - all tables exist")

    return None


@db.command("stats")
@click.option("--db", type=click.Path(), help="Database path")
def db_stats(db: str | None) -> None:
    """Show database statistics."""
    from snore.constants import DEFAULT_DATABASE_PATH
    from snore.database.session import init_database, session_scope
    from snore.services.database_service import DatabaseService

    if db:
        init_database(str(Path(db)))
        db_path = Path(db)
    else:
        init_database()
        db_path = Path(DEFAULT_DATABASE_PATH)

    with session_scope() as session:
        service = DatabaseService(session)
        stats = service.get_stats(str(db_path))

        click.echo("\n📊 Database Statistics")
        click.echo(f"{'=' * 50}")
        click.echo(f"Database: {stats.db_path}")
        click.echo(f"Size: {stats.size_mb:.1f} MB")

        click.echo("\nRow Counts")
        click.echo(f"  Profiles: {stats.profile_count}")
        click.echo(f"  Devices: {stats.device_count}")
        click.echo(f"  Sessions: {stats.session_count}")
        click.echo(f"  Days: {stats.day_count}")
        click.echo(f"  Events: {stats.event_count}")
        click.echo(f"  Waveforms: {stats.waveform_count}")
        click.echo(f"  Analysis Results: {stats.analysis_count}")
        click.echo(f"  Detected Patterns: {stats.pattern_count}")

        click.echo("\nData Coverage")
        click.echo(
            f"  Sessions with waveforms: {stats.sessions_with_waveforms}/{stats.session_count} ({stats.waveform_coverage_pct:.1f}%)"
        )
        click.echo(
            f"  Sessions with events: {stats.sessions_with_events}/{stats.session_count} ({stats.event_coverage_pct:.1f}%)"
        )
        click.echo(
            f"  Sessions analyzed: {stats.analysis_count}/{stats.session_count} ({stats.analysis_coverage_pct:.1f}%)"
        )

        if stats.first_session and stats.last_session:
            click.echo(
                f"\nDate range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
            )

        click.echo(f"{'=' * 50}\n")


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
@click.confirmation_option(prompt="Are you sure you want to vacuum the database?")
def vacuum(db: str | None) -> None:
    """Optimize database (reclaim space after deletions)."""
    from sqlalchemy import text

    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    click.echo("Vacuuming database...")

    with session_scope() as session:
        session.execute(text("VACUUM"))
        session.commit()

    click.echo("✓ Database vacuumed successfully")


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def drop(db: str | None, force: bool) -> None:
    """Drop database (permanently delete all CPAP data)."""

    from snore.constants import DEFAULT_DATABASE_PATH
    from snore.database.session import cleanup_database, init_database, session_scope

    if db:
        db_path = Path(db)
    else:
        db_path = Path(DEFAULT_DATABASE_PATH)

    if not db_path.exists():
        click.echo(f"Database does not exist at {db_path}")
        return

    try:
        init_database(str(db_path))
        with session_scope() as session:
            from snore.services.database_service import DatabaseService

            service = DatabaseService(session)
            stats = service.get_stats(str(db_path))

            click.echo(f"\nDatabase: {db_path}")
            click.echo(f"Size: {stats.size_mb:.1f} MB")
            click.echo(f"Devices: {stats.device_count}")
            click.echo(f"Sessions: {stats.session_count}")
            click.echo(f"Events: {stats.event_count:,}")

            if stats.first_session and stats.last_session:
                click.echo(
                    f"Date range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
                )

    except Exception as e:
        click.echo(f"Warning: Could not read database stats: {e}")

    if not force:
        click.echo("\n⚠️  WARNING: This will permanently delete all CPAP data!")
        if not click.confirm(
            "Are you sure you want to drop the database?", default=False
        ):
            click.echo("Database drop cancelled")
            return

    try:
        cleanup_database()
    except Exception as e:
        click.echo(f"Warning during cleanup: {e}")

    try:
        if db_path.exists():
            db_path.unlink()
            click.echo(f"\n✓ Deleted database: {db_path}")

        for ext in ["-wal", "-shm"]:
            wal_file = Path(str(db_path) + ext)
            if wal_file.exists():
                wal_file.unlink()
                click.echo(f"✓ Deleted: {wal_file.name}")

        click.echo("\nDatabase dropped successfully")

    except Exception as e:
        click.echo(f"Error dropping database: {e}", err=True)
        sys.exit(1)


@cli.group()
def session() -> None:
    """Session management commands."""
    pass


@session.command("list")
@click.option("--device", "-d", help="Filter by device serial number")
@click.option(
    "--from",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_LIST_SESSIONS_LIMIT,
    help="Max sessions to show (use 0 for all)",
)
@click.option(
    "--sort-by",
    type=click.Choice(["date-asc", "date-desc", "session-id", "duration"]),
    default="date-desc",
    help="Sort order for results (default: date-desc)",
)
@click.option("--all", "show_all", is_flag=True, help="Include disabled sessions")
@click.option("--db", type=click.Path(), help="Database path")
def session_list(
    device: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
    limit: int,
    sort_by: str,
    show_all: bool,
    db: str | None,
) -> None:
    """List imported sessions."""
    from snore.database.session import init_database, session_scope
    from snore.services.session_service import SessionService

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        service = SessionService(db_session)
        result = service.list_sessions(
            device=device,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            sort_by=sort_by,
            include_disabled=show_all,
        )

        if not result.sessions:
            click.echo("No sessions found")
            return

        click.echo(
            f"{'ID':<5} {'Date':<12} {'Time':<8} {'Duration':<10} {'Device':<30} {'Serial':<15} {'AHI':<8}"
        )
        click.echo("-" * 100)

        for sess in result.sessions:
            device_name = f"{sess.manufacturer} {sess.model}"
            ahi_str = f"{sess.ahi:.1f}" if sess.ahi is not None else "N/A"
            status_marker = "" if sess.enabled else "[disabled]"

            click.echo(
                f"{sess.id:<5} "
                f"{sess.start_time:%Y-%m-%d}   {sess.start_time:%H:%M:%S}  "
                f"{sess.duration_hours:>6.1f}h    "
                f"{device_name:<30} "
                f"{sess.serial_number:<15} "
                f"{ahi_str:<8} {status_marker}"
            )

        if result.total_count > 0 and limit > 0 and result.total_count > limit:
            click.echo(
                f"\nShowing {len(result.sessions)} of {result.total_count} sessions"
            )
            click.echo(
                f"Tip: Use '--limit {result.total_count}' or '--limit 0' to show all"
            )
        else:
            click.echo(f"\nShowing all {len(result.sessions)} sessions")


@session.command("show")
@click.argument("session_id", type=int)
@click.option("--settings", "show_settings", is_flag=True, help="Show device settings")
@click.option("--db", type=click.Path(), help="Database path")
def session_show(session_id: int, show_settings: bool, db: str | None) -> None:
    """Show details for a specific session."""
    from snore.database.session import init_database, session_scope
    from snore.services.session_service import SessionService

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        service = SessionService(db_session)

        try:
            detail = service.get_session_detail(
                session_id, include_settings=show_settings
            )
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        click.echo(f"\nSession ID: {detail.id}")
        click.echo(f"  Device Session ID: {detail.device_session_id}")

        if detail.device_manufacturer and detail.device_model:
            click.echo(
                f"  Device: {detail.device_manufacturer} {detail.device_model} (SN: {detail.device_serial})"
            )

        click.echo(f"  Start: {detail.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"  End: {detail.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(
            f"  Duration: {detail.duration_hours:.2f}h ({detail.duration_seconds}s)"
        )

        if detail.therapy_mode:
            click.echo(f"  Therapy Mode: {detail.therapy_mode}")

        click.echo("\n  Data:")
        click.echo(f"    Events: {detail.event_count}")
        click.echo(f"    Waveforms: {detail.waveform_count}")
        if detail.waveform_types:
            click.echo(
                f"    Available types: {', '.join(sorted(detail.waveform_types))}"
            )
        click.echo(f"    Has Statistics: {detail.has_statistics}")
        click.echo(f"    Has Event Data: {detail.has_event_data}")

        stats = detail.statistics
        if stats:
            click.echo("\n  Statistics:")

            if stats.usage_hours is not None:
                click.echo(f"    Usage: {stats.usage_hours:.1f}h")

            has_event_indices = any(
                [
                    stats.ahi is not None,
                    stats.rei is not None,
                    stats.oai is not None,
                    stats.cai is not None,
                    stats.hi is not None,
                ]
            )
            if has_event_indices:
                click.echo("\n    Event Indices:")
                if stats.ahi is not None:
                    click.echo(f"      AHI: {stats.ahi:.1f}")
                if stats.rei is not None:
                    click.echo(f"      REI: {stats.rei:.1f}")
                if stats.oai is not None:
                    click.echo(f"      OAI: {stats.oai:.1f}")
                if stats.cai is not None:
                    click.echo(f"      CAI: {stats.cai:.1f}")
                if stats.hi is not None:
                    click.echo(f"      HI: {stats.hi:.1f}")

            has_event_counts = any(
                [
                    (stats.obstructive_apneas or 0) > 0,
                    (stats.central_apneas or 0) > 0,
                    (stats.mixed_apneas or 0) > 0,
                    (stats.hypopneas or 0) > 0,
                    (stats.reras or 0) > 0,
                    (stats.flow_limitations or 0) > 0,
                ]
            )
            if has_event_counts:
                click.echo("\n    Event Counts:")
                if stats.obstructive_apneas and stats.obstructive_apneas > 0:
                    click.echo(f"      Obstructive Apneas: {stats.obstructive_apneas}")
                if stats.central_apneas and stats.central_apneas > 0:
                    click.echo(f"      Central Apneas: {stats.central_apneas}")
                if stats.mixed_apneas and stats.mixed_apneas > 0:
                    click.echo(f"      Mixed Apneas: {stats.mixed_apneas}")
                if stats.hypopneas and stats.hypopneas > 0:
                    click.echo(f"      Hypopneas: {stats.hypopneas}")
                if stats.reras and stats.reras > 0:
                    click.echo(f"      RERAs: {stats.reras}")
                if stats.flow_limitations and stats.flow_limitations > 0:
                    click.echo(f"      Flow Limitations: {stats.flow_limitations}")

            has_pressure = any(
                [
                    stats.pressure_mean is not None,
                    stats.pressure_min is not None,
                    stats.pressure_max is not None,
                    stats.pressure_95th is not None,
                ]
            )
            if has_pressure:
                click.echo("\n    Pressure:")
                if stats.pressure_mean is not None:
                    click.echo(f"      Mean: {stats.pressure_mean:.1f} cmH₂O")
                if stats.pressure_min is not None and stats.pressure_max is not None:
                    click.echo(
                        f"      Range: {stats.pressure_min:.1f} - {stats.pressure_max:.1f} cmH₂O"
                    )
                if stats.pressure_95th is not None:
                    click.echo(
                        f"      95th percentile: {stats.pressure_95th:.1f} cmH₂O"
                    )

            has_epap = any(
                [
                    stats.epap_mean is not None,
                    stats.epap_min is not None,
                    stats.epap_max is not None,
                    stats.epap_95th is not None,
                ]
            )
            if has_epap:
                click.echo("\n    EPAP:")
                if stats.epap_mean is not None:
                    click.echo(f"      Mean: {stats.epap_mean:.1f} cmH₂O")
                if stats.epap_min is not None and stats.epap_max is not None:
                    click.echo(
                        f"      Range: {stats.epap_min:.1f} - {stats.epap_max:.1f} cmH₂O"
                    )
                if stats.epap_95th is not None:
                    click.echo(f"      95th percentile: {stats.epap_95th:.1f} cmH₂O")

            has_leak = any(
                [
                    stats.leak_mean is not None,
                    stats.leak_percentile_70 is not None,
                    stats.leak_95th is not None,
                ]
            )
            if has_leak:
                click.echo("\n    Leak:")
                if stats.leak_mean is not None:
                    click.echo(f"      Mean: {stats.leak_mean:.1f} L/min")
                if stats.leak_percentile_70 is not None:
                    click.echo(
                        f"      70th percentile: {stats.leak_percentile_70:.1f} L/min"
                    )
                if stats.leak_95th is not None:
                    click.echo(f"      95th percentile: {stats.leak_95th:.1f} L/min")

            has_spo2 = any(
                [
                    stats.spo2_mean is not None,
                    stats.spo2_min is not None,
                    stats.spo2_time_below_90 is not None,
                ]
            )
            if has_spo2:
                click.echo("\n    SpO₂:")
                if stats.spo2_mean is not None:
                    click.echo(f"      Mean: {stats.spo2_mean:.1f}%")
                if stats.spo2_min is not None:
                    click.echo(f"      Minimum: {stats.spo2_min:.0f}%")
                if stats.spo2_time_below_90 is not None:
                    minutes_below_90 = stats.spo2_time_below_90 / 60
                    click.echo(f"      Time below 90%: {minutes_below_90:.1f} minutes")

            has_pulse = any(
                [
                    stats.pulse_mean is not None,
                    stats.pulse_min is not None,
                    stats.pulse_max is not None,
                ]
            )
            if has_pulse:
                click.echo("\n    Pulse:")
                if stats.pulse_mean is not None:
                    click.echo(f"      Mean: {stats.pulse_mean:.1f} BPM")
                if stats.pulse_min is not None and stats.pulse_max is not None:
                    click.echo(
                        f"      Range: {stats.pulse_min:.0f} - {stats.pulse_max:.0f} BPM"
                    )

            has_respiratory = any(
                [
                    stats.respiratory_rate_mean is not None,
                    stats.tidal_volume_mean is not None,
                    stats.minute_ventilation_mean is not None,
                ]
            )
            if has_respiratory:
                click.echo("\n    Respiratory:")
                if stats.respiratory_rate_mean is not None:
                    click.echo(
                        f"      Mean Respiratory Rate: {stats.respiratory_rate_mean:.1f} breaths/min"
                    )
                if stats.tidal_volume_mean is not None:
                    click.echo(
                        f"      Mean Tidal Volume: {stats.tidal_volume_mean:.0f} mL"
                    )
                if stats.minute_ventilation_mean is not None:
                    click.echo(
                        f"      Mean Minute Ventilation: {stats.minute_ventilation_mean:.1f} L/min"
                    )

        if detail.settings:
            click.echo("\n  Settings:")
            import pint

            ureg = pint.get_application_registry()  # type: ignore[no-untyped-call]
            for s in detail.settings:
                if s.key == "tube_temp" and s.value:
                    try:
                        temp_c = ureg.Quantity(float(s.value), ureg.degC)
                        temp_f = temp_c.to(ureg.degF)
                        click.echo(f"    {s.key}: {temp_f.magnitude:.1f}°F")
                    except (ValueError, TypeError):
                        click.echo(f"    {s.key}: {s.value}")
                else:
                    click.echo(f"    {s.key}: {s.value}")
        elif show_settings:
            click.echo("\n  Settings: None recorded")

        click.echo()


@session.command("delete")
@click.option("--device", "-d", help="Filter by device serial number")
@click.option(
    "--session-id",
    "session_ids",
    type=str,
    help="Comma-separated session IDs to delete (e.g., '1,2,3')",
)
@click.option(
    "--from",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete sessions from this date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete sessions up to this date (YYYY-MM-DD)",
)
@click.option("--all", "delete_all", is_flag=True, help="Delete all sessions")
@click.option(
    "--dry-run", is_flag=True, help="Preview what would be deleted without deleting"
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option("--db", type=click.Path(), help="Database path")
def session_delete(
    device: str | None,
    session_ids: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
    delete_all: bool,
    dry_run: bool,
    force: bool,
    db: str | None,
) -> int | None:
    """Delete sessions from the database."""
    from snore.database.session import init_database, session_scope
    from snore.services.session_service import SessionService

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    id_list = None
    if session_ids:
        try:
            id_list = [int(sid.strip()) for sid in session_ids.split(",")]
        except ValueError:
            click.echo(
                "❌ Error: Invalid session ID format. Use comma-separated integers (e.g., '1,2,3')",
                err=True,
            )
            return 1

    with session_scope() as db_session:
        service = SessionService(db_session)

        try:
            preview = service.get_delete_preview(
                device=device,
                session_ids=id_list,
                from_date=from_date,
                to_date=to_date,
                delete_all=delete_all,
            )
        except ValueError as e:
            click.echo(f"❌ Error: {e}", err=True)
            return 1

        if not preview.sessions:
            click.echo("⚠️  No sessions found matching the specified criteria")
            return 0

        click.echo(f"\n{'=' * 80}")
        if dry_run:
            click.echo("🔍 DRY RUN MODE - No data will be deleted")
        else:
            click.echo("⚠️  Sessions to be DELETED")
        click.echo(f"{'=' * 80}\n")

        click.echo(
            f"{'ID':<5} {'Date':<12} {'Time':<8} {'Duration':<10} {'Device':<30} {'Serial':<15}"
        )
        click.echo("-" * 80)

        for sess in preview.sessions:
            device_name = f"{sess.manufacturer} {sess.model}"

            click.echo(
                f"{sess.id:<5} "
                f"{sess.start_time:%Y-%m-%d}   {sess.start_time:%H:%M:%S}  "
                f"{sess.duration_hours:>6.1f}h    "
                f"{device_name:<30} "
                f"{sess.serial_number:<15}"
            )

        click.echo("\n" + "=" * 80)
        click.echo("📊 Deletion Summary")
        click.echo("=" * 80)
        click.echo(f"Sessions:    {len(preview.sessions)}")
        click.echo(f"Events:      {preview.event_count}")
        click.echo(f"Waveforms:   {preview.waveform_count}")
        click.echo(f"Statistics:  {preview.stats_count}")
        click.echo("=" * 80 + "\n")

        if dry_run:
            click.echo("✓ Dry run complete. Use without --dry-run to delete.")
            return 0

        if not force:
            click.echo("⚠️  WARNING: This action cannot be undone!")
            if not click.confirm("Are you sure you want to delete these sessions?"):
                click.echo("Deletion cancelled")
                return 0

        session_ids_to_delete = [s.id for s in preview.sessions]
        deleted_count = service.delete_sessions(session_ids_to_delete)

        click.echo(
            f"\n✓ Successfully deleted {deleted_count} session(s) and related data"
        )

        if deleted_count > 10:
            click.echo("\n💡 Tip: Run 'snore db vacuum' to reclaim disk space")

        return 0


def _toggle_session(session_id: int, enabled: bool, db: str | None) -> None:
    """Enable or disable a session and recalculate day statistics."""
    from snore.database.session import init_database, session_scope
    from snore.services.session_service import SessionService

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        service = SessionService(db_session)

        try:
            detail = service.get_session_detail(session_id)
            if detail.enabled == enabled:
                status = "enabled" if enabled else "disabled"
                click.echo(f"Session {session_id} is already {status}")
                return

            service.set_session_enabled(session_id, enabled)

            status = "enabled" if enabled else "disabled"
            click.echo(f"Session {session_id} {status} and day statistics recalculated")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@session.command("enable")
@click.argument("session_id", type=int)
@click.option("--db", type=click.Path(), help="Database path")
def session_enable(session_id: int, db: str | None) -> None:
    """Enable a session and recalculate day statistics."""
    _toggle_session(session_id, enabled=True, db=db)


@session.command("disable")
@click.argument("session_id", type=int)
@click.option("--db", type=click.Path(), help="Database path")
def session_disable(session_id: int, db: str | None) -> None:
    """Disable a session and recalculate day statistics."""
    _toggle_session(session_id, enabled=False, db=db)


@cli.group()
def analysis() -> None:
    """Analyze CPAP sessions and view results."""
    pass


@analysis.command("run")
@click.option("--session-id", type=int, help="Analyze single session by ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Analyze single session by date (YYYY-MM-DD)",
)
@click.option(
    "--from",
    "start",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date for batch analysis (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "end",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date for batch analysis (YYYY-MM-DD)",
)
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--no-store", is_flag=True, help="Don't store results in database")
@click.option(
    "--debug-events",
    is_flag=True,
    help="Print detailed comparison of machine vs programmatic event detection",
)
@click.option(
    "--mode",
    "-m",
    multiple=True,
    default=None,
    help="Detection mode(s) to run. Default: aasm. Can specify multiple: -m aasm -m resmed",
)
@click.option(
    "--all-modes",
    is_flag=True,
    help="Run all available detection modes",
)
@click.option(
    "--plain",
    is_flag=True,
    help="Plain output without colors/borders",
)
def run(
    session_id: int | None,
    date: datetime | None,
    start: datetime | None,
    end: datetime | None,
    db: str | None,
    no_store: bool,
    debug_events: bool,
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> int | None:
    """Run analysis on CPAP sessions."""
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    single_session_flags = [session_id is not None, date is not None]
    batch_flags = [start is not None, end is not None]

    single_count = sum(single_session_flags)
    batch_count = sum(batch_flags)

    if single_count > 1:
        click.echo("Error: --session-id and --date are mutually exclusive", err=True)
        sys.exit(1)

    if single_count > 0 and batch_count > 0:
        click.echo(
            "Error: Single session flags (--session-id, --date) cannot be used with batch flags (--from, --to)",
            err=True,
        )
        sys.exit(1)

    if single_count == 0 and batch_count == 0:
        click.echo(
            "Error: Must provide at least one selection flag (--session-id, --date, --from, or --to)",
            err=True,
        )
        sys.exit(1)

    with session_scope() as session:
        if single_count > 0:
            _analyze_single_session(
                session,
                None,
                session_id,
                date,
                no_store,
                debug_events,
                mode,
                all_modes,
                plain,
            )
        else:
            _analyze_batch(
                session,
                None,
                start,
                end,
                start is None and end is None,
                no_store,
                debug_events,
                mode,
                all_modes,
                plain,
            )

    return None


@analysis.command("list")
@click.option(
    "--from",
    "start",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date for filtering (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "end",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date for filtering (YYYY-MM-DD)",
)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_LIST_SESSIONS_LIMIT,
    help="Max sessions to show (use 0 for all)",
)
@click.option("--analyzed-only", is_flag=True, help="Show only analyzed sessions")
@click.option(
    "--sort-by",
    type=click.Choice(["date-asc", "date-desc", "session-id"]),
    default="date-desc",
    help="Sort order for results (default: date-desc)",
)
@click.option("--db", type=click.Path(), help="Database path")
def list_cmd(
    start: datetime | None,
    end: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str,
    db: str | None,
) -> None:
    """List sessions with analysis status."""
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        _list_sessions(session, start, end, limit, analyzed_only, sort_by)


@analysis.command("show")
@click.option("--session-id", type=int, help="Show analysis for session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Show analysis for session on date (YYYY-MM-DD)",
)
@click.option("--db", type=click.Path(), help="Database path")
@click.option(
    "--plain",
    is_flag=True,
    help="Plain output without colors/borders",
)
def show(
    session_id: int | None,
    date: datetime | None,
    db: str | None,
    plain: bool,
) -> None:
    """Display stored analysis results."""
    from snore.analysis.service import AnalysisService
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    if session_id is None and date is None:
        click.echo("Error: Must provide either --session-id or --date", err=True)
        sys.exit(1)

    if session_id is not None and date is not None:
        click.echo("Error: --session-id and --date are mutually exclusive", err=True)
        sys.exit(1)

    with session_scope() as session:
        if date is not None:
            db_session = (
                session.query(models.Session)
                .join(models.Day)
                .filter(models.Day.date == date.date())
                .first()
            )
            if not db_session:
                click.echo(f"Error: No session found for date {date.date()}", err=True)
                sys.exit(1)
            session_id = db_session.id

        assert session_id is not None, "session_id should not be None"

        db_session = session.query(models.Session).filter_by(id=session_id).first()
        if not db_session:
            click.echo(f"Error: Session {session_id} not found", err=True)
            sys.exit(1)

        day_date = (
            db_session.day.date if db_session.day else db_session.start_time.date()
        )
        session_date_str = day_date.isoformat()

        analysis_service = AnalysisService(session)
        result = analysis_service.get_analysis_result(session_id)

        if result is None:
            click.echo(f"Error: No analysis found for session {session_id}", err=True)
            sys.exit(1)

        click.echo(f"Displaying stored analysis for session {session_id}...\n")
        _display_analysis_result(result, plain, session_date_str)


def _get_validation_metrics(
    mode_result: "ModeResult",
    machine_events: "list[AnalysisEvent]",
    mode: str,
) -> dict[str, Any]:
    """
    Get validation metrics comparing programmatic vs machine events.

    Args:
        mode_result: Detection mode results with programmatic events
        machine_events: Machine-detected events from CPAP device
        mode: Detection mode name (for looking up correct config)

    Returns:
        Dictionary with validation results including false positives/negatives
    """
    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.analysis.modes.config import AASM_CONFIG
    from snore.analysis.modes.detector import EventDetector
    from snore.analysis.shared.types import ApneaEvent, HypopneaEvent
    from snore.analysis.utils import convert_machine_events

    machine_apneas, machine_hypopneas = convert_machine_events(machine_events)

    config = AVAILABLE_CONFIGS.get(mode, AASM_CONFIG)
    detector = EventDetector(config)
    validation = detector.validate_against_machine_events(
        mode_result.apneas,
        mode_result.hypopneas,
        machine_apneas,
        machine_hypopneas,
    )

    false_negatives: list[AnalysisEvent] = []

    for machine_event in machine_events:
        is_matched = False
        machine_relative_time = machine_event.start_time
        all_programmatic = list(mode_result.apneas) + list(mode_result.hypopneas)

        for prog_event in all_programmatic:
            time_diff = abs(prog_event.start_time - machine_relative_time)
            if time_diff <= 5.0:
                is_matched = True
                break

        if not is_matched:
            false_negatives.append(machine_event)

    false_positives: list[ApneaEvent | HypopneaEvent] = []

    for prog_event in list(mode_result.apneas) + list(mode_result.hypopneas):
        is_matched = False

        for machine_event in machine_events:
            machine_relative_time = machine_event.start_time
            time_diff = abs(prog_event.start_time - machine_relative_time)
            if time_diff <= 5.0:
                is_matched = True
                break

        if not is_matched:
            false_positives.append(prog_event)

    return {
        "apnea_validation": validation["apnea_validation"],
        "hypopnea_validation": validation["hypopnea_validation"],
        "false_negatives": false_negatives,
        "false_positives": false_positives,
    }


def _display_analysis_result(
    result: "AnalysisResult", plain: bool, session_date: str
) -> None:
    """Display analysis results with machine comparison."""
    from snore.utils.display import (
        create_console,
        create_flow_limitation_panel,
        create_header_panel,
        create_machine_events_table,
        create_mode_comparison_table,
        create_validation_table,
        format_event_list,
    )

    con = create_console(plain)
    con.print("✓ Analysis complete\n")

    header = create_header_panel(session_date, result.session_duration_hours, plain)
    con.print(header)
    con.print()

    machine_events = result.machine_events
    if machine_events:
        machine_table = create_machine_events_table(
            machine_events, result.session_duration_hours, plain
        )
        con.print(machine_table)
        con.print()

    if result.mode_results:
        mode_table = create_mode_comparison_table(result.mode_results, plain)
        con.print(mode_table)
        con.print()

    if machine_events and result.mode_results:
        con.print(
            "[bold]VALIDATION vs MACHINE EVENTS[/bold]"
            if not plain
            else "VALIDATION vs MACHINE EVENTS"
        )
        con.print()

        for mode_name, mode_result in result.mode_results.items():
            validation = _get_validation_metrics(mode_result, machine_events, mode_name)

            val_table = create_validation_table(
                mode_name,
                validation["apnea_validation"],
                validation["hypopnea_validation"],
                machine_events,
                plain,
            )
            con.print(val_table)

            if validation["false_negatives"]:
                fn_text = format_event_list(
                    validation["false_negatives"],
                    "  Missed events",
                    format_time_offset,
                )
                con.print(fn_text)

            if validation["false_positives"]:
                fp_text = format_event_list(
                    validation["false_positives"],
                    "  Extra events",
                    format_time_offset,
                )
                con.print(fp_text)

            con.print()

    if result.flow_analysis:
        flow_panel, flow_table = create_flow_limitation_panel(
            result.flow_analysis, plain
        )
        con.print(flow_panel)
        con.print(flow_table)
        con.print()


def _analyze_single_session(
    session: Any,
    prof: Any,
    session_id: int | None,
    date: datetime | None,
    no_store: bool,
    debug_events: bool,
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> None:
    """Analyze a single session and display detailed report."""
    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.analysis.service import AnalysisService
    from snore.database import models

    if date:
        db_session = (
            session.query(models.Session)
            .join(models.Day)
            .filter(models.Day.date == date.date())
            .first()
        )
        if not db_session:
            click.echo(f"Error: No session found for {date.date()}", err=True)
            sys.exit(1)
        session_id = db_session.id
        session_date_str = date.date().isoformat()
    else:
        db_session = session.query(models.Session).filter_by(id=session_id).first()
        if not db_session:
            click.echo(f"Error: Session {session_id} not found", err=True)
            sys.exit(1)
        day_date = (
            db_session.day.date if db_session.day else db_session.start_time.date()
        )
        session_date_str = day_date.isoformat()

    click.echo(f"\nAnalyzing session {session_date_str} (ID: {session_id})...")

    analysis_service = AnalysisService(session)

    assert session_id is not None, "session_id should not be None"

    modes = None
    if all_modes:
        modes = list(AVAILABLE_CONFIGS.keys())
    elif mode:
        modes = list(mode)

    try:
        result = analysis_service.analyze_session(
            session_id=session_id,
            modes=modes,
            store_results=not no_store,
            debug=debug_events,
        )
        _display_analysis_result(result, plain, session_date_str)

    except Exception as e:
        click.echo(f"\nAnalysis failed: {e}", err=True)
        logger.error("Analysis error", exc_info=True)
        sys.exit(1)


def _analyze_batch(
    session: Any,
    prof: Any,
    start: datetime | None,
    end: datetime | None,
    analyze_all: bool,
    no_store: bool,
    debug_events: bool,
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> None:
    """Analyze multiple sessions with progress bar."""
    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.analysis.service import AnalysisService
    from snore.database import models

    query = session.query(models.Session).join(models.Day)

    if not analyze_all:
        if start:
            query = query.filter(models.Day.date >= start.date())
        if end:
            query = query.filter(models.Day.date <= end.date())

    sessions = query.order_by(models.Day.date).all()

    if not sessions:
        click.echo("No sessions found for the specified criteria")
        return

    modes = None
    if all_modes:
        modes = list(AVAILABLE_CONFIGS.keys())
    elif mode:
        modes = list(mode)

    click.echo(f"\nAnalyzing {len(sessions)} sessions...")
    modes_display = modes if modes else ["aasm"]
    click.echo(f"  Modes: {', '.join(modes_display)}")

    analysis_service = AnalysisService(session)
    successful = 0
    failed = 0

    with click.progressbar(sessions, label="Analyzing") as bar:
        for db_session in bar:
            try:
                analysis_service.analyze_session(
                    session_id=db_session.id,
                    modes=modes,
                    store_results=not no_store,
                    debug=debug_events,
                )
                successful += 1
            except Exception as e:
                failed += 1
                logger.debug(f"Failed to analyze session {db_session.id}: {e}")

    click.echo("\n✓ Analysis complete")
    click.echo(f"  Successful: {successful}")
    click.echo(f"  Failed: {failed}")


def _list_sessions(
    session: Any,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str = "date-desc",
) -> None:
    """List sessions and their analysis status."""
    from snore.services.analysis_facade import AnalysisFacade

    facade = AnalysisFacade(session)
    results = facade.list_sessions_with_status(
        start=start, end=end, limit=limit, analyzed_only=analyzed_only, sort_by=sort_by
    )

    if not results:
        click.echo("No sessions found")
        return

    click.echo(
        f"{'Date':<12} {'ID':<6} {'Duration':<10} {'Analyzed':<10} {'Analysis ID':<12}"
    )
    click.echo("-" * 60)

    for item in results:
        duration = f"{item.duration_hours:.1f}h" if item.duration_hours else "N/A"
        analyzed_str = "✓" if item.has_analysis else "✗"
        analysis_id_str = str(item.analysis_id) if item.analysis_id else "-"

        click.echo(
            f"{item.session_date!s:<12} {item.session_id:<6} {duration:<10} "
            f"{analyzed_str:<10} {analysis_id_str:<12}"
        )

    if analyzed_only and len(results) > 0:
        click.echo(f"\nShowing {len(results)} analyzed session(s)")


@analysis.command("delete")
@click.option(
    "--session-id",
    "session_ids",
    type=str,
    help="Comma-separated session IDs to delete analysis for (e.g., '1,2,3')",
)
@click.option(
    "--from",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete analysis for sessions from this date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete analysis for sessions up to this date (YYYY-MM-DD)",
)
@click.option("--all", "delete_all", is_flag=True, help="Delete all analysis results")
@click.option(
    "--all-versions",
    is_flag=True,
    help="Delete all analysis versions (default: only latest)",
)
@click.option(
    "--dry-run", is_flag=True, help="Preview what would be deleted without deleting"
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option("--db", type=click.Path(), help="Database path")
def analysis_delete(
    session_ids: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
    delete_all: bool,
    all_versions: bool,
    dry_run: bool,
    force: bool,
    db: str | None,
) -> int | None:
    """Delete analysis results without deleting the sessions themselves."""
    from snore.database.session import init_database, session_scope
    from snore.services.analysis_facade import AnalysisFacade

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    if not any([session_ids, from_date, to_date, delete_all]):
        raise click.ClickException(
            "You must specify at least one filter:\n"
            "  • --session-id <ids>\n"
            "  • --from <date>\n"
            "  • --to <date>\n"
            "  • --all"
        )

    id_list: list[int] | None = None
    if session_ids:
        try:
            id_list = [int(sid.strip()) for sid in session_ids.split(",")]
        except ValueError:
            click.echo(
                "❌ Error: Invalid session ID format. Use comma-separated integers (e.g., '1,2,3')",
                err=True,
            )
            return 1

    with session_scope() as session:
        facade = AnalysisFacade(session)

        try:
            preview = facade.get_delete_preview(
                session_ids=id_list,
                from_date=from_date,
                to_date=to_date,
                delete_all=delete_all,
                all_versions=all_versions,
            )
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            return 1

        if preview.sessions_with_analysis == 0:
            click.echo(
                "⚠️  No sessions with analysis results found matching the specified criteria"
            )
            return 0

        click.echo(f"\n{'=' * 80}")
        if dry_run:
            click.echo("🔍 DRY RUN MODE - No data will be deleted")
        else:
            click.echo("⚠️  Analysis Results to be DELETED")
        click.echo(f"{'=' * 80}\n")

        click.echo(
            f"{'Sess ID':<8} {'Date':<12} {'Time':<8} {'Versions':<10} {'Device':<25}"
        )
        click.echo("-" * 80)

        for detail in preview.session_details:
            start = detail.start_time
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            device_name = f"{detail.manufacturer} {detail.model}"

            click.echo(
                f"{detail.id:<8} "
                f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
                f"{detail.version_count:<10} "
                f"{device_name:<25}"
            )

        click.echo("\n" + "=" * 80)
        click.echo("📊 Deletion Summary")
        click.echo("=" * 80)
        click.echo(f"Sessions with analysis:          {preview.sessions_with_analysis}")
        click.echo(
            f"Total analysis records:          {preview.total_analysis_records}"
            + (
                " (all versions)"
                if all_versions
                or preview.total_analysis_records == preview.sessions_with_analysis
                else ""
            )
        )
        click.echo(
            f"Analysis records to delete:      {preview.records_to_delete}"
            + (
                " (latest only)"
                if not all_versions
                and preview.total_analysis_records > preview.sessions_with_analysis
                else ""
            )
        )
        click.echo(
            f"Detected patterns to delete:     {preview.patterns_count} (cascade delete)"
        )
        click.echo("=" * 80 + "\n")

        if dry_run:
            click.echo("✓ Dry run complete. Use without --dry-run to delete.")
            return 0

        if not force:
            click.echo(
                "⚠️  WARNING: This will delete analysis results but keep the sessions!"
            )
            if not click.confirm(
                "Are you sure you want to delete these analysis results?"
            ):
                click.echo("Deletion cancelled")
                return 0

        session_ids_to_delete = [d.id for d in preview.session_details]
        deleted_count = facade.delete_analysis(session_ids_to_delete, all_versions)

        click.echo(
            f"\n✓ Successfully deleted {deleted_count} analysis record(s) for {preview.sessions_with_analysis} session(s)"
        )

        if deleted_count > 10:
            click.echo("\n💡 Tip: Run 'snore db vacuum' to reclaim disk space")

        return 0


@cli.group()
def completions() -> None:
    """Manage shell tab completion."""
    pass


_SUPPORTED_SHELLS = ["bash", "zsh"]


@completions.command(name="bash")
def completions_bash() -> None:
    """Output bash completion script for manual installation."""
    from snore.completions import generate_completion_script

    try:
        script = generate_completion_script("bash")
        click.echo(script)
        click.echo("\nTo install: Add the above to your ~/.bashrc or run:")
        click.echo("\nsnore completions install")
    except Exception as e:
        click.echo(f"Error generating completion script: {e}", err=True)
        sys.exit(1)


@completions.command(name="zsh")
def completions_zsh() -> None:
    """Output zsh completion script for manual installation."""
    from snore.completions import generate_completion_script

    try:
        script = generate_completion_script("zsh")
        click.echo(script)
        click.echo("\nTo install: Add the above to your ~/.zshrc or run:")
        click.echo("\nsnore completions install")
    except Exception as e:
        click.echo(f"Error generating completion script: {e}", err=True)
        sys.exit(1)


@completions.command(name="install")
@click.option(
    "--shell",
    type=click.Choice(_SUPPORTED_SHELLS, case_sensitive=False),
    help="Shell type (auto-detected if not specified)",
)
def completions_install(shell: str | None) -> None:
    """Install shell completion to config file."""
    from snore.completions import detect_shell, install_completion

    if shell is None:
        shell = detect_shell()
        if shell is None:
            click.echo(
                "Error: Could not detect shell. Please specify with --shell", err=True
            )
            sys.exit(1)
        click.echo(f"Detected shell: {shell}")

    success, message = install_completion(shell, dry_run=False)

    if success:
        click.echo(f"✓ {message}")
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)


@completions.command(name="uninstall")
@click.option(
    "--shell",
    type=click.Choice(_SUPPORTED_SHELLS, case_sensitive=False),
    help="Shell type (auto-detected if not specified)",
)
def completions_uninstall(shell: str | None) -> None:
    """Remove shell completion from config file."""
    from snore.completions import (
        detect_shell,
        find_config_file,
        uninstall_completion,
    )

    if shell is None:
        shell = detect_shell()
        if shell is None:
            click.echo(
                "Error: Could not detect shell. Please specify with --shell", err=True
            )
            sys.exit(1)

    config_path = find_config_file(shell)
    if config_path is None:
        click.echo(f"Error: No {shell} config file found", err=True)
        sys.exit(1)

    success, message = uninstall_completion(config_path)

    if success:
        click.echo(f"✓ {message}")
    else:
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)


@cli.group()
def logs() -> None:
    """Log file management commands."""
    pass


@logs.command("path")
def logs_path() -> None:
    """Show log file location."""
    from snore.logging_config import get_log_path

    log_path = get_log_path()
    click.echo(f"Log file: {log_path}")

    if log_path.exists():
        size_mb = log_path.stat().st_size / (1024 * 1024)
        click.echo(f"Size: {size_mb:.2f} MB")

        import glob

        log_dir = log_path.parent
        backup_files = sorted(glob.glob(str(log_dir / "snore.log.*")))
        if backup_files:
            click.echo(f"Backup files: {len(backup_files)}")
    else:
        click.echo("(File does not exist yet)")


@logs.command("show")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output (like tail -f)")
def logs_show(lines: int, follow: bool) -> None:
    """Show recent log entries."""
    from snore.logging_config import get_log_path

    log_path = get_log_path()

    if not log_path.exists():
        click.echo("No log file found", err=True)
        sys.exit(1)

    if follow:
        import subprocess

        try:
            subprocess.run(["tail", "-f", str(log_path)], check=True)
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            click.echo("Error: 'tail' command not found", err=True)
            sys.exit(1)
    else:
        try:
            with open(log_path, encoding="utf-8") as f:
                all_lines = f.readlines()
                display_lines = (
                    all_lines[-lines:] if len(all_lines) > lines else all_lines
                )
                for line in display_lines:
                    click.echo(line.rstrip())
        except Exception as e:
            click.echo(f"Error reading log file: {e}", err=True)
            sys.exit(1)


@logs.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear all log files?")
def logs_clear() -> None:
    """Clear all log files."""
    import glob

    from snore.logging_config import get_log_path

    log_path = get_log_path()
    log_dir = log_path.parent

    if not log_dir.exists():
        click.echo("No log directory found")
        return

    log_files = [log_path] + [Path(f) for f in glob.glob(str(log_dir / "snore.log.*"))]

    removed_count = 0
    for log_file in log_files:
        if log_file.exists():
            try:
                log_file.unlink()
                removed_count += 1
            except Exception as e:
                click.echo(f"Failed to remove {log_file}: {e}", err=True)

    if removed_count > 0:
        click.echo(f"Removed {removed_count} log file(s)")
    else:
        click.echo("No log files to remove")


@cli.command()
@click.option(
    "--from",
    "date_from",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "date_to",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--mode",
    "-m",
    default="aasm",
    type=str,
    help="Detection mode to validate (default: aasm)",
)
@click.option(
    "--export",
    type=click.Path(),
    help="Export report to file (.json or .csv)",
)
@click.option("--db", type=click.Path(exists=True), help="Database path")
def validate(
    date_from: datetime,
    date_to: datetime,
    mode: str,
    export: str | None,
    db: str | None,
) -> None:
    """
    Run batch validation across multiple sessions.

    Validates SNORE's detection against machine events for sessions in the specified
    date range, and displays aggregate metrics.
    """
    from pathlib import Path

    from snore.database.session import init_database, session_scope
    from snore.validation import BatchValidator, export_report_csv, export_report_json

    if date_from > date_to:
        click.echo("Error: --from date must be before or equal to --to date", err=True)
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        try:
            validator = BatchValidator(db_session, None)

            click.echo(
                f"Running validation from {date_from.date()} to {date_to.date()}..."
            )
            click.echo(f"Mode: {mode}\n")

            report = validator.validate_date_range(
                date_from.strftime("%Y-%m-%d"),
                date_to.strftime("%Y-%m-%d"),
                mode=mode,
            )

            click.echo("=" * 60)
            click.echo("VALIDATION REPORT")
            click.echo("=" * 60)
            click.echo(
                f"Date Range: {report.date_range_start} to {report.date_range_end}"
            )
            click.echo(f"Sessions Analyzed: {report.aggregate.total_sessions}")
            click.echo(f"Total Machine Events: {report.aggregate.total_machine_events}")
            click.echo(
                f"Total Programmatic Events: {report.aggregate.total_programmatic_events}"
            )

            click.echo("\nAggregate Metrics:")
            click.echo(
                f"  Apneas:     "
                f"Avg Sens: {report.aggregate.avg_apnea_sensitivity * 100:.0f}%  "
                f"Avg Prec: {report.aggregate.avg_apnea_precision * 100:.0f}%  "
                f"Avg F1: {report.aggregate.avg_apnea_f1:.2f}"
            )
            click.echo(
                f"  Hypopneas:  "
                f"Avg Sens: {report.aggregate.avg_hypopnea_sensitivity * 100:.0f}%  "
                f"Avg Prec: {report.aggregate.avg_hypopnea_precision * 100:.0f}%  "
                f"Avg F1: {report.aggregate.avg_hypopnea_f1:.2f}"
            )

            if report.aggregate.low_sensitivity_sessions:
                click.echo(
                    f"\nSessions with Low Sensitivity (<60%): "
                    f"{len(report.aggregate.low_sensitivity_sessions)}"
                )
                click.echo(
                    f"  Session IDs: {report.aggregate.low_sensitivity_sessions[:10]}"
                )
                if len(report.aggregate.low_sensitivity_sessions) > 10:
                    click.echo(
                        f"  ... and {len(report.aggregate.low_sensitivity_sessions) - 10} more"
                    )

            click.echo("\nPer-Session Results:")
            click.echo(
                f"{'Date':<12} {'ID':<6} {'Machine':<8} {'Prog':<8} {'Apnea Sens':<11} {'Hypopnea Sens':<13}"
            )
            click.echo("=" * 60)

            for session in report.sessions[:10]:
                click.echo(
                    f"{session.date:<12} "
                    f"{session.session_id:<6} "
                    f"{session.machine_event_count:<8} "
                    f"{session.programmatic_event_count:<8} "
                    f"{session.apnea_sensitivity * 100:>6.0f}%     "
                    f"{session.hypopnea_sensitivity * 100:>6.0f}%"
                )

            if len(report.sessions) > 10:
                click.echo(f"... and {len(report.sessions) - 10} more sessions")

            if export:
                export_path = Path(export)
                if export_path.suffix == ".json":
                    export_report_json(report, export_path)
                    click.echo(f"\nReport exported to {export_path}")
                elif export_path.suffix == ".csv":
                    export_report_csv(report, export_path)
                    click.echo(f"\nReport exported to {export_path}")
                else:
                    click.echo(
                        f"Error: Unknown export format '{export_path.suffix}'. "
                        f"Use .json or .csv",
                        err=True,
                    )

        except Exception as e:
            click.echo(f"Validation error: {e}", err=True)
            if "--verbose" in sys.argv or "-v" in sys.argv:
                import traceback

                traceback.print_exc()
            sys.exit(1)


@cli.group()
def event() -> None:
    """Event data export commands."""
    pass


@event.command("export")
@click.option(
    "--session-id",
    type=int,
    help="Session ID to export events from",
)
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Export events from session on this date (YYYY-MM-DD)",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(),
    help="Output CSV file path",
)
@click.option("--db", type=click.Path(exists=True), help="Database path")
@click.option(
    "--mode",
    "-m",
    default="aasm",
    type=str,
    help="Detection mode to export (default: aasm)",
)
def export_events(
    session_id: int | None,
    date: datetime | None,
    output: str,
    db: str | None,
    mode: str,
) -> None:
    """
    Export event data to CSV for comparison with OSCAR.

    Exports both machine-detected and programmatic events with timestamps,
    types, durations, and match status.
    """
    import csv

    from pathlib import Path

    from snore.analysis.service import AnalysisService
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if not session_id and not date:
        click.echo("Error: Must specify either --session-id or --date", err=True)
        sys.exit(1)

    if session_id and date:
        click.echo(
            "Error: Cannot specify both --session-id and --date. Choose one.",
            err=True,
        )
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        try:
            if date:
                sessions = (
                    db_session.query(models.Session)
                    .filter(
                        models.Session.start_time >= date,
                        models.Session.start_time < date + timedelta(days=1),
                    )
                    .all()
                )

                if not sessions:
                    click.echo(f"Error: No session found on {date.date()}", err=True)
                    sys.exit(1)

                if len(sessions) > 1:
                    click.echo(
                        f"Warning: Multiple sessions found on {date.date()}, using first one"
                    )

                session_id = sessions[0].id

            assert session_id is not None, "session_id must be set"

            analysis_service = AnalysisService(db_session)
            result = analysis_service.get_analysis_result(session_id)

            if not result:
                click.echo(f"Running analysis for session {session_id}...")
                result = analysis_service.analyze_session(session_id, modes=[mode])

            if mode not in result.mode_results:
                click.echo(
                    f"Error: Mode {mode} not found in analysis results", err=True
                )
                sys.exit(1)

            mode_result = result.mode_results[mode]
            machine_events = result.machine_events

            from snore.database import models

            session = db_session.get(models.Session, session_id)
            if not session:
                click.echo(f"Error: Session {session_id} not found", err=True)
                sys.exit(1)

            export_events_list = []

            for event in machine_events:
                time_offset = event.start_time
                absolute_time = session.start_time + timedelta(seconds=time_offset)
                export_events_list.append(
                    {
                        "timestamp": absolute_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "time_into_session": format_time_offset(time_offset),
                        "event_type": abbreviate_event_type(event.event_type),
                        "duration_sec": f"{event.duration:.1f}",
                        "source": "machine",
                        "matched": "?",
                    }
                )

            for apnea in mode_result.apneas:
                time_offset = apnea.start_time
                absolute_time = session.start_time + timedelta(seconds=time_offset)
                export_events_list.append(
                    {
                        "timestamp": absolute_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "time_into_session": format_time_offset(time_offset),
                        "event_type": apnea.event_type,
                        "duration_sec": f"{apnea.duration:.1f}",
                        "source": "programmatic",
                        "matched": "?",
                    }
                )

            for hypopnea in mode_result.hypopneas:
                time_offset = hypopnea.start_time
                absolute_time = session.start_time + timedelta(seconds=time_offset)
                export_events_list.append(
                    {
                        "timestamp": absolute_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "time_into_session": format_time_offset(time_offset),
                        "event_type": "H",
                        "duration_sec": f"{hypopnea.duration:.1f}",
                        "source": "programmatic",
                        "matched": "?",
                    }
                )

            export_events_list.sort(key=lambda x: x["time_into_session"])

            from snore.services.event_service import EventService

            machine_times = [
                parse_time_offset(e["time_into_session"])
                for e in export_events_list
                if e["source"] == "machine"
            ]
            prog_times = [
                parse_time_offset(e["time_into_session"])
                for e in export_events_list
                if e["source"] == "programmatic"
            ]

            event_service = EventService()
            machine_matched, prog_matched = event_service.classify_matches(
                machine_times, prog_times
            )

            machine_match_map = dict(
                zip(sorted(machine_times), machine_matched, strict=True)
            )
            prog_match_map = dict(zip(sorted(prog_times), prog_matched, strict=True))

            for event_dict in export_events_list:
                time_offset = parse_time_offset(event_dict["time_into_session"])
                if event_dict["source"] == "machine":
                    is_matched = machine_match_map[time_offset]
                else:
                    is_matched = prog_match_map[time_offset]
                event_dict["matched"] = "yes" if is_matched else "no"

            output_path = Path(output)
            with open(output_path, "w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "time_into_session",
                        "event_type",
                        "duration_sec",
                        "source",
                        "matched",
                    ],
                )
                writer.writeheader()
                writer.writerows(export_events_list)

            click.echo(f"Exported {len(export_events_list)} events to {output_path}")
            click.echo(f"  Machine events: {len(machine_events)}")
            click.echo(
                f"  Programmatic events: {len(mode_result.apneas) + len(mode_result.hypopneas)}"
            )

        except Exception as e:
            click.echo(f"Export error: {e}", err=True)
            if "--verbose" in sys.argv or "-v" in sys.argv:
                import traceback

                traceback.print_exc()
            sys.exit(1)


@cli.group()
def waveform() -> None:
    """Waveform inspection and visualization commands."""
    pass


def _resolve_session_id(
    db_session: Any,
    session_id: int | None,
    date: datetime | None,
) -> int:
    """
    Resolve session ID from either explicit ID or date.

    Args:
        db_session: Database session
        session_id: Explicit session ID (takes precedence)
        date: Date to look up session

    Returns:
        Resolved session ID

    Raises:
        SystemExit: If session cannot be resolved
    """
    from snore.services.session_service import SessionService

    service = SessionService(db_session)

    try:
        return service.resolve_session_id(session_id, date)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@waveform.command("list")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@click.option("--db", type=click.Path(), help="Database path")
def list_waveforms(
    session_id: int | None,
    date: datetime | None,
    db: str | None,
) -> None:
    """
    List available waveform types for a session.

    Shows all waveform data available for the specified session, including
    sample rates, sample counts, units, and durations.

    Examples:
        snore waveform list --session-id 37
        snore waveform list --date 2025-10-25
    """
    from snore.database.session import init_database, session_scope
    from snore.services.waveform_service import WaveformService

    if session_id is None and date is None:
        click.echo("Error: Either --session-id or --date must be provided", err=True)
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        resolved_id = _resolve_session_id(db_session, session_id, date)

        service = WaveformService(db_session)
        waveforms = service.list_waveforms(resolved_id)

        if not waveforms:
            click.echo(f"No waveforms found for session {resolved_id}")
            return

        click.echo(f"Available waveforms for session {resolved_id}:")
        click.echo(
            f"  {'TYPE':<12} {'RATE':<12} {'SAMPLES':<10} {'UNIT':<10} {'DURATION'}"
        )

        for wf in waveforms:
            unit = wf.unit or "?"
            rate_str = f"{wf.sample_rate:.1f}Hz"

            click.echo(
                f"  {wf.waveform_type:<12} "
                f"{rate_str:<12} "
                f"{wf.sample_count:<10} "
                f"{unit:<10} "
                f"{wf.duration_hours:.1f}h"
            )


@waveform.command("show")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@click.option("--time", required=True, help="Time offset (HH:MM:SS)")
@click.option(
    "--window", type=int, default=60, help="Window size in seconds (default: 60)"
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["plot", "csv"]),
    default="plot",
    help="Output format (plot=interactive graph, csv=data export)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path (required for csv format)",
)
@click.option("--db", type=click.Path(), help="Database path")
@click.option(
    "--mode", "-m", default="aasm", help="Detection mode to compare (default: aasm)"
)
@click.option(
    "--type",
    "waveform_type",
    default="flow",
    help="Waveform type to display (default: flow)",
)
def show_waveform(
    session_id: int | None,
    date: datetime | None,
    time: str,
    window: int,
    output_format: str,
    output: str | None,
    db: str | None,
    mode: str,
    waveform_type: str,
) -> None:
    """
    Display waveform at a specific time.

    View waveform data centered on a specific time offset to visually
    inspect detected respiratory events (for flow waveforms).

    Examples:
        snore waveform show --session-id 37 --time 05:56:22 --window 30
        snore waveform show --date 2025-10-25 --time 01:25:16 --type pressure
        snore waveform show --session-id 37 --time 01:25:16 --format csv --output waveform.csv
    """
    from snore.analysis.service import AnalysisService
    from snore.database.session import init_database, session_scope
    from snore.waveform import WaveformInspector, WaveformRenderer

    if session_id is None and date is None:
        click.echo("Error: Either --session-id or --date must be provided", err=True)
        sys.exit(1)

    if output_format == "csv" and output is None:
        click.echo("Error: --output is required for csv format", err=True)
        sys.exit(1)

    waveform_types = [t.strip() for t in waveform_type.split(",")]

    if output_format == "csv" and len(waveform_types) > 1:
        click.echo("Error: CSV export only supports single waveform type", err=True)
        sys.exit(1)

    if len(waveform_types) > 4:
        click.echo("Error: Maximum 4 waveform types supported", err=True)
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    try:
        center_seconds = parse_time_offset(time)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    with session_scope() as db_session:
        session_id = _resolve_session_id(db_session, session_id, date)

        inspector = WaveformInspector(db_session)

        if len(waveform_types) == 1:
            waveform_type_single = waveform_types[0]
            try:
                timestamps, values, metadata = inspector.get_window(
                    session_id=session_id,
                    center_seconds=center_seconds,
                    window_seconds=float(window),
                    waveform_type=waveform_type_single,
                )
            except Exception as e:
                click.echo(f"Error loading waveform: {e}", err=True)
                sys.exit(1)

            if len(timestamps) == 0:
                click.echo("No data in window", err=True)
                sys.exit(1)

            machine_events = []
            programmatic_events = []

            if waveform_type_single == "flow":
                analysis_service = AnalysisService(db_session)
                try:
                    result = analysis_service.get_analysis_result(session_id)
                except Exception:
                    result = None

                if result:
                    start_time = center_seconds - window / 2
                    end_time = center_seconds + window / 2

                    if result.machine_events:
                        machine_events = inspector.find_events_in_window(
                            result.machine_events, start_time, end_time
                        )

                    if mode in result.mode_results:
                        mode_result = result.mode_results[mode]
                        all_prog_events = list(mode_result.apneas) + list(
                            mode_result.hypopneas
                        )
                        programmatic_events = inspector.find_events_in_window(
                            all_prog_events, start_time, end_time
                        )

            if output_format == "plot":
                show_events = waveform_type_single == "flow"
                renderer = WaveformRenderer(
                    width=80, height=20, show_events=show_events
                )
                renderer.render(
                    timestamps=timestamps,
                    values=values,
                    machine_events=machine_events,
                    programmatic_events=programmatic_events,
                    session_id=session_id,
                    center_time=time,
                    waveform_type=waveform_type_single,
                )

            elif output_format == "csv":
                import csv

                assert output is not None
                column_name = f"{waveform_type_single}_value"
                with open(output, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["timestamp_seconds", column_name])
                    for ts, value in zip(timestamps, values, strict=True):
                        writer.writerow([f"{ts:.3f}", f"{value:.3f}"])

                click.echo(f"Exported {len(timestamps)} samples to {output}")

        else:
            waveform_data = []
            for wf_type in waveform_types:
                try:
                    timestamps, values, metadata = inspector.get_window(
                        session_id=session_id,
                        center_seconds=center_seconds,
                        window_seconds=float(window),
                        waveform_type=wf_type,
                    )
                    if len(timestamps) > 0:
                        waveform_data.append((timestamps, values, wf_type))
                    else:
                        click.echo(
                            f"Warning: No data for waveform type '{wf_type}'", err=True
                        )
                except Exception as e:
                    click.echo(
                        f"Warning: Failed to load waveform type '{wf_type}': {e}",
                        err=True,
                    )

            if not waveform_data:
                click.echo("Error: No waveform data loaded", err=True)
                sys.exit(1)

            renderer = WaveformRenderer(width=80, height=20, show_events=False)
            renderer.render_multi(
                waveform_data=waveform_data,
                session_id=session_id,
                center_time=time,
            )


@waveform.command("compare")
@click.option("--session-id", type=int, help="Session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Session date (YYYY-MM-DD)",
)
@click.option(
    "--mode", "-m", default="aasm", help="Detection mode to compare (default: aasm)"
)
@click.option("--show-unmatched", is_flag=True, help="Only show unmatched events")
@click.option("--db", type=click.Path(), help="Database path")
def compare_events(
    session_id: int | None,
    date: datetime | None,
    mode: str,
    show_unmatched: bool,
    db: str | None,
) -> None:
    """
    Compare machine vs programmatic events with waveform inspection commands.

    Lists false positives and false negatives with commands to inspect each event.

    Examples:
        snore waveform compare --session-id 37 --mode aasm
        snore waveform compare --date 2025-10-25 --mode resmed --show-unmatched
    """
    from snore.analysis.service import AnalysisService
    from snore.analysis.utils import convert_machine_events
    from snore.database.session import init_database, session_scope

    if session_id is None and date is None:
        click.echo("Error: Either --session-id or --date must be provided", err=True)
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        session_id = _resolve_session_id(db_session, session_id, date)

        analysis_service = AnalysisService(db_session)
        try:
            result = analysis_service.get_analysis_result(session_id)
        except Exception as e:
            click.echo(f"Error loading analysis: {e}", err=True)
            sys.exit(1)

        if result is None:
            click.echo(
                f"Error: No analysis results found for session {session_id}", err=True
            )
            sys.exit(1)

        if mode not in result.mode_results:
            click.echo(f"Error: Mode {mode} not found in analysis results", err=True)
            sys.exit(1)

        mode_result = result.mode_results[mode]

        machine_events = result.machine_events or []
        machine_apneas, machine_hypopneas = convert_machine_events(machine_events)

        prog_apneas = list(mode_result.apneas)
        prog_hypopneas = list(mode_result.hypopneas)

        false_negatives = []
        false_positives_apnea = []
        false_positives_hypopnea = []

        for m_event in machine_apneas + machine_hypopneas:
            machine_relative_time = m_event.start_time
            is_matched = False

            for p_event in prog_apneas + prog_hypopneas:
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_negatives.append(m_event)

        for p_event in prog_apneas:
            is_matched = False

            for m_event in machine_apneas + machine_hypopneas:
                machine_relative_time = m_event.start_time
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_positives_apnea.append(p_event)

        for p_event in prog_hypopneas:
            is_matched = False

            for m_event in machine_apneas + machine_hypopneas:
                machine_relative_time = m_event.start_time
                if abs(p_event.start_time - machine_relative_time) <= 5.0:
                    is_matched = True
                    break

            if not is_matched:
                false_positives_hypopnea.append(p_event)

        click.echo(f"Session {session_id} - Event Comparison ({mode} mode)")
        click.echo(
            f"Machine: {len(machine_events)} events | Programmatic: {len(prog_apneas) + len(prog_hypopneas)} events"
        )
        click.echo("")

        if not show_unmatched or len(false_negatives) > 0:
            click.echo(
                f"FALSE NEGATIVES (machine events missed by programmatic): {len(false_negatives)}"
            )
            for event in false_negatives:
                time_str = format_time_offset(event.start_time)
                event_type = getattr(event, "event_type", "H")
                click.echo(f"  {event_type} at {time_str} ({event.duration:.1f}s)")
                click.echo(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )
            click.echo("")

        if (
            not show_unmatched
            or len(false_positives_apnea) + len(false_positives_hypopnea) > 0
        ):
            click.echo(
                f"FALSE POSITIVES (programmatic events not in machine): {len(false_positives_apnea) + len(false_positives_hypopnea)}"
            )

            for event in false_positives_apnea:
                time_str = format_time_offset(event.start_time)
                event_type = event.event_type
                conf = getattr(event, "confidence", 0)
                flow_red = getattr(event, "flow_reduction", 0)
                click.echo(
                    f"  {event_type} at {time_str} (conf: {conf:.2f}, flow_red: {flow_red * 100:.0f}%)"
                )
                click.echo(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )

            for event in false_positives_hypopnea:
                time_str = format_time_offset(event.start_time)
                conf = getattr(event, "confidence", 0)
                flow_red = getattr(event, "flow_reduction", 0)
                click.echo(
                    f"  H at {time_str} (conf: {conf:.2f}, flow_red: {flow_red * 100:.0f}%)"
                )
                click.echo(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )


@cli.group()
def rx() -> None:
    """RX (prescription) settings tracking and analysis."""
    pass


@rx.command("history")
@click.option("--db", type=click.Path(), help="Database path")
def rx_history(db: str | None) -> None:
    """
    Show RX settings history with average outcomes.

    Displays all prescription periods in chronological order with settings
    and key metrics like average AHI and therapy hours.

    Example:
        snore rx history
    """
    from pathlib import Path

    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)

        click.echo("RX Settings History")
        click.echo("=" * 80)

        for i, period in enumerate(stats_periods, 1):
            days_count = len(period.days)
            end_str = (
                period.end_date.strftime("%Y-%m-%d")
                if i < len(stats_periods)
                else "present"
            )

            click.echo(
                f"\nPeriod {i}: {period.start_date.strftime('%Y-%m-%d')} to {end_str} ({days_count} days)"
            )

            mode = period.settings.get("mode", "?")
            epr_level = period.settings.get("epr_level", "?")
            epr_mode = period.settings.get("epr_mode", "?")

            if "pressure_min" in period.settings and "pressure_max" in period.settings:
                pressure_str = f"{period.settings['pressure_min']}-{period.settings['pressure_max']} cmH2O"
            elif "pressure_fixed" in period.settings:
                pressure_str = f"{period.settings['pressure_fixed']} cmH2O (Fixed)"
            else:
                pressure_str = "?"

            click.echo(
                f"  Mode: {mode} | Pressure: {pressure_str} | EPR: {epr_level} {epr_mode}"
            )

            if period.avg_ahi is not None:
                click.echo(f"  Avg AHI: {period.avg_ahi:.1f}", nl=False)
            else:
                click.echo("  Avg AHI: N/A", nl=False)

            if period.avg_hours is not None:
                click.echo(f" | Avg Hours: {period.avg_hours:.1f}", nl=False)

            if period.avg_leak is not None:
                click.echo(f" | Avg Leak: {period.avg_leak:.1f}")
            else:
                click.echo()

        click.echo("\n" + "=" * 80)


@rx.command("current")
@click.option("--db", type=click.Path(), help="Database path")
def rx_current(db: str | None) -> None:
    """
    Show current RX settings period.

    Displays the most recent prescription settings along with outcomes.

    Example:
        snore rx current
    """
    from pathlib import Path

    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)
        current = stats_periods[-1]

        days_count = len(current.days)

        click.echo("Current RX Settings")
        click.echo("=" * 80)
        click.echo(
            f"Period: {current.start_date.strftime('%Y-%m-%d')} to present ({days_count} days)"
        )

        mode = current.settings.get("mode", "?")
        epr_level = current.settings.get("epr_level", "?")
        epr_mode = current.settings.get("epr_mode", "?")

        if "pressure_min" in current.settings and "pressure_max" in current.settings:
            pressure_str = f"{current.settings['pressure_min']}-{current.settings['pressure_max']} cmH2O"
        elif "pressure_fixed" in current.settings:
            pressure_str = f"{current.settings['pressure_fixed']} cmH2O (Fixed)"
        else:
            pressure_str = "?"

        click.echo(f"\nMode: {mode}")
        click.echo(f"Pressure: {pressure_str}")
        click.echo(f"EPR: {epr_level} {epr_mode}")

        click.echo("\nOutcomes:")
        if current.avg_ahi is not None:
            click.echo(f"  Avg AHI: {current.avg_ahi:.1f}")
        else:
            click.echo("  Avg AHI: N/A")

        if current.median_ahi is not None:
            click.echo(f"  Median AHI: {current.median_ahi:.1f}")

        if current.avg_hours is not None:
            click.echo(f"  Avg Hours: {current.avg_hours:.1f}")

        if current.avg_leak is not None:
            click.echo(f"  Avg Leak: {current.avg_leak:.1f}")

        click.echo("=" * 80)


@rx.command("compare")
@click.option("--db", type=click.Path(), help="Database path")
@click.option(
    "--min-days",
    type=int,
    default=7,
    help="Minimum days for period to be included (default: 7)",
)
def rx_compare(db: str | None, min_days: int) -> None:
    """
    Compare RX periods and identify best/worst settings.

    Shows a table of all prescription periods with statistics side-by-side
    and highlights the best and worst periods based on average AHI.

    Example:
        snore rx compare
        snore rx compare --min-days 14
    """
    from pathlib import Path

    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)

        if len(stats_periods) < 2:
            click.echo(
                "At least 2 periods are needed for comparison. Use 'snore rx history' to view the single period."
            )
            return

        best, worst = tracker.best_worst(stats_periods, min_days=min_days)

        click.echo("RX Period Comparison")
        click.echo("=" * 100)
        click.echo(
            f"{'Dates':<25} {'Days':<6} {'Avg AHI':<10} {'Avg Leak':<10} {'Mode':<8} {'Pressure':<15} {'EPR':<10}"
        )
        click.echo("=" * 100)

        for idx, period in enumerate(stats_periods):
            days_count = len(period.days)
            start_str = period.start_date.strftime("%Y-%m-%d")
            end_str = (
                period.end_date.strftime("%Y-%m-%d")
                if idx < len(stats_periods) - 1
                else "present"
            )
            date_range = f"{start_str}..{end_str}"

            mode = period.settings.get("mode", "?")[:7]
            epr = f"{period.settings.get('epr_level', '?')} {period.settings.get('epr_mode', '?')[:2]}"

            if "pressure_min" in period.settings and "pressure_max" in period.settings:
                pressure_str = f"{period.settings['pressure_min']}-{period.settings['pressure_max']}"
            elif "pressure_fixed" in period.settings:
                pressure_str = f"{period.settings['pressure_fixed']} (F)"
            else:
                pressure_str = "?"

            ahi_str = f"{period.avg_ahi:.1f}" if period.avg_ahi is not None else "N/A"
            leak_str = (
                f"{period.avg_leak:.1f}" if period.avg_leak is not None else "N/A"
            )

            marker = ""
            if best and period is best:
                marker = "  <- Best"
            elif worst and period is worst:
                marker = "  <- Worst"

            click.echo(
                f"{date_range:<25} {days_count:<6} {ahi_str:<10} {leak_str:<10} {mode:<8} {pressure_str:<15} {epr:<10}{marker}"
            )

        click.echo("=" * 100)

        if best:
            click.echo(f"\nBest Period (Avg AHI: {best.avg_ahi:.1f}):")
            click.echo(
                f"  {best.start_date.strftime('%Y-%m-%d')} to {best.end_date.strftime('%Y-%m-%d')} ({len(best.days)} days)"
            )
            click.echo(f"  Settings: {best.settings}")

        if worst:
            click.echo(f"\nWorst Period (Avg AHI: {worst.avg_ahi:.1f}):")
            click.echo(
                f"  {worst.start_date.strftime('%Y-%m-%d')} to {worst.end_date.strftime('%Y-%m-%d')} ({len(worst.days)} days)"
            )
            click.echo(f"  Settings: {worst.settings}")


def main() -> None:
    """Main CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
