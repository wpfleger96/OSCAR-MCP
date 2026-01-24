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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from snore.analysis.modes.types import ModeResult
    from snore.analysis.service import AnalysisResult
    from snore.analysis.types import AnalysisEvent

import click

from snore.config import (
    get_config_path,
    get_default_profile,
    set_default_profile,
    unset_default_profile,
)
from snore.constants import (
    DEFAULT_LIST_SESSIONS_LIMIT,
    abbreviate_event_type,
)
from snore.logging_config import setup_logging
from snore.parsers.register_all import register_all_parsers
from snore.parsers.registry import parser_registry

logger = logging.getLogger(__name__)

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


def ensure_profile(username: str) -> int:
    """Get or create profile by username, return profile_id."""
    from snore.database import models
    from snore.database.session import session_scope

    with session_scope() as session:
        profile = session.query(models.Profile).filter_by(username=username).first()
        if not profile:
            profile = models.Profile(
                username=username, settings={"day_split_time": "12:00:00"}
            )
            session.add(profile)
            session.flush()
        return profile.id


def resolve_profile(explicit_profile: str | None, db_session: "Session") -> str:
    """
    Resolve profile using precedence: CLI > config > auto-detect.

    Args:
        explicit_profile: Value from --profile flag (None if not provided)
        db_session: Active database session

    Returns:
        Username to use

    Raises:
        click.ClickException: If profile cannot be resolved
    """
    from snore.database import models

    if explicit_profile:
        return explicit_profile

    config_profile = get_default_profile()
    if config_profile:
        prof = (
            db_session.query(models.Profile).filter_by(username=config_profile).first()
        )
        if prof:
            return config_profile
        else:
            click.echo(
                f"Warning: Default profile '{config_profile}' not found in database.",
                err=True,
            )
            click.echo(
                "Update with: snore profile set-default <name>",
                err=True,
            )

    profiles = db_session.query(models.Profile).all()
    if len(profiles) == 1:
        username: str = profiles[0].username
        return username

    if len(profiles) == 0:
        raise click.ClickException(
            "No profiles found. Import data first: snore import <path>"
        )
    else:
        profile_list = ", ".join([p.username for p in profiles])
        raise click.ClickException(
            f"Multiple profiles found ({profile_list}). "
            "Specify --profile <name> or set default: "
            "snore profile set-default <name>"
        )


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

    selected_results = []
    if len(results) > 1:
        click.echo(f"\nFound {len(results)} data sources:\n")
        for i, (parser, detection) in enumerate(results, 1):
            meta = detection.metadata or {}
            if meta.get("profile_name"):
                desc = f"{meta['profile_name']} ({meta.get('structure_type', 'unknown').replace('_', ' ')})"
            else:
                structure = meta.get("structure_type", "raw SD card").replace("_", " ")
                serial = meta.get("device_serial", "unknown")
                desc = f"{structure} - S/N: {serial}"

            click.echo(f"  {i}. {parser.manufacturer} - {desc}")
            if meta.get("data_root"):
                click.echo(f"     Path: {meta['data_root']}")

        click.echo(f"  {len(results) + 1}. Import all")

        choice = click.prompt("\nSelect which to import", type=int, default=1)

        if choice == len(results) + 1:
            selected_results = results
        elif 1 <= choice <= len(results):
            selected_results = [results[choice - 1]]
        else:
            click.echo(f"❌ Invalid choice: {choice}", err=True)
            return 1
    else:
        selected_results = results

    init_database(str(Path(db)) if db else None)

    with session_scope() as session:
        orphaned_count = SessionImporter.cleanup_orphaned_records(session)
        if orphaned_count > 0:
            click.echo(f"⚠️  Cleaned up {orphaned_count} orphaned records from database")

    total_imported = 0
    total_skipped = 0
    total_failed = 0

    for parser, detection in selected_results:
        meta = detection.metadata or {}
        source_desc = (
            meta.get("profile_name") or f"S/N {meta.get('device_serial', 'unknown')}"
        )

        if len(selected_results) > 1:
            click.echo(f"\n{'=' * 60}")
            click.echo(f"Processing: {source_desc}")
            click.echo(f"{'=' * 60}")

        click.echo(f"✓ Detected: {parser.manufacturer} ({parser.parser_id})")
        click.echo(
            f"  Structure: {meta.get('structure_type', 'unknown').replace('_', ' ')}"
        )
        if meta.get("data_root"):
            click.echo(f"  Data root: {meta['data_root']}")

        profile_id = None
        if meta.get("profile_name"):
            profile_id = ensure_profile(meta["profile_name"])
            click.echo(f"  Profile: {meta['profile_name']}")

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
            sessions = list(
                parser.parse_sessions(
                    data_path,
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
            if len(selected_results) > 1:
                continue
            return 1

        if not sessions:
            click.echo("⚠️  No sessions found")
            if len(selected_results) > 1:
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
            if len(selected_results) == 1:
                click.echo("\n✓ Dry run complete. Use without --dry-run to import.")
            continue

        importer = SessionImporter(profile_id=profile_id)

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

        if len(selected_results) > 1:
            click.echo(f"\n{'=' * 50}")
            click.echo(f"📊 Summary for {source_desc}")
            click.echo(f"{'=' * 50}")
            click.echo(f"✓ Imported: {imported} sessions")
            if skipped > 0:
                click.echo(f"⊝ Skipped:  {skipped} sessions")
            if failed > 0:
                click.echo(f"❌ Failed:   {failed} sessions")

    if dry_run and len(selected_results) > 1:
        click.echo(f"\n{'=' * 50}")
        click.echo("📊 Overall Dry Run Summary")
        click.echo(f"{'=' * 50}")
        click.echo(f"✓ Total data sources: {len(selected_results)}")
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
@click.option("--profile", type=str, help="Filter to specific profile")
@click.option("--days", type=int, help="Limit to last N days")
def stats(db: str | None, profile: str | None, days: int | None) -> None:
    """Show therapy usage and clinical statistics."""
    from datetime import date, timedelta

    from sqlalchemy import func, text

    from snore.analysis.calculations import (
        assess_therapy_effectiveness,
        calculate_average_ahi,
    )
    from snore.database import models
    from snore.database.session import init_database, session_scope

    init_database(str(Path(db)) if db else None)

    with session_scope() as session:
        query = session.query(models.Day)

        if profile:
            query = query.join(models.Profile).filter(
                models.Profile.username == profile
            )

        if days:
            cutoff_date = date.today() - timedelta(days=days)
            query = query.filter(models.Day.date >= cutoff_date)

        day_records = query.all()

        if not day_records:
            click.echo("\n📈 Therapy Statistics")
            click.echo(f"{'=' * 50}")
            click.echo("\nNo therapy data found.")
            click.echo(f"{'=' * 50}\n")
            return

        sessions_by_profile = session.execute(
            text("""
            SELECT p.username, COUNT(s.id) as session_count
            FROM profiles p
            LEFT JOIN days d ON d.profile_id = p.id
            LEFT JOIN sessions s ON s.day_id = d.id
            GROUP BY p.id, p.username
            ORDER BY session_count DESC
        """)
        ).fetchall()

        dates = [d.date for d in day_records]
        first_date = min(dates)
        last_date = max(dates)
        days_since_last = (date.today() - last_date).days

        day_ids = [d.id for d in day_records]
        days_with_data = len(day_records)

        total_duration = (
            session.query(func.sum(models.Session.duration_seconds))
            .join(models.Day)
            .filter(models.Day.id.in_(day_ids))
            .scalar()
        )
        total_hours = (total_duration or 0) / 3600
        avg_hours = total_hours / days_with_data if days_with_data > 0 else 0

        avg_ahi = calculate_average_ahi(day_records)
        effectiveness = assess_therapy_effectiveness(avg_ahi) if avg_ahi else "unknown"

        pressure_values = [
            d.pressure_median for d in day_records if d.pressure_median is not None
        ]
        avg_pressure = (
            sum(pressure_values) / len(pressure_values) if pressure_values else None
        )
        min_pressure = min(pressure_values) if pressure_values else None
        max_pressure = max(pressure_values) if pressure_values else None

        leak_values = [d.leak_median for d in day_records if d.leak_median is not None]
        avg_leak = sum(leak_values) / len(leak_values) if leak_values else None

        spo2_values = [d.spo2_mean for d in day_records if d.spo2_mean is not None]
        avg_spo2 = sum(spo2_values) / len(spo2_values) if spo2_values else None
        spo2_mins = [d.spo2_min for d in day_records if d.spo2_min is not None]
        min_spo2 = min(spo2_mins) if spo2_mins else None

        event_counts = (
            session.query(
                models.Event.event_type, func.count(models.Event.id).label("count")
            )
            .join(models.Session)
            .join(models.Day)
            .filter(models.Day.id.in_(day_ids))
            .group_by(models.Event.event_type)
            .order_by(text("count DESC"))
            .all()
        )

        total_events = sum(count for _, count in event_counts)

        click.echo("\n📈 Therapy Statistics")
        click.echo(f"{'=' * 50}")

        if not profile:
            click.echo("\nProfiles")
            for username, count in sessions_by_profile:
                click.echo(f"  {username}: {count} sessions")

        click.echo("\nDate Range")
        click.echo(f"  First session: {first_date}")
        click.echo(f"  Last session: {last_date}")
        click.echo(f"  Days since last use: {days_since_last}")

        click.echo("\nUsage")
        click.echo(f"  Total therapy hours: {total_hours:,.1f} hrs")
        click.echo(f"  Average per night: {avg_hours:.1f} hrs")
        click.echo(f"  Days with data: {days_with_data}")

        click.echo("\nClinical")
        if avg_ahi is not None:
            click.echo(f"  Average AHI: {avg_ahi:.1f}")
        else:
            click.echo("  Average AHI: N/A")
        click.echo(f"  Effectiveness: {effectiveness}")

        if avg_pressure is not None:
            click.echo("\nPressure")
            click.echo(f"  Average: {avg_pressure:.1f} cmH₂O")
            if min_pressure is not None and max_pressure is not None:
                click.echo(f"  Range: {min_pressure:.1f} - {max_pressure:.1f} cmH₂O")

        if avg_leak is not None:
            click.echo("\nLeak")
            click.echo(f"  Average: {avg_leak:.1f} L/min")
            leak_assessment = "well controlled" if avg_leak < 24 else "elevated"
            click.echo(f"  Assessment: {leak_assessment}")

        if avg_spo2 is not None:
            click.echo("\nSpO₂")
            click.echo(f"  Average: {avg_spo2:.1f}%")
            if min_spo2 is not None:
                click.echo(f"  Minimum recorded: {min_spo2:.0f}%")

        if event_counts:
            click.echo("\nEvents")
            for event_type, count in event_counts:
                pct = (count / total_events * 100) if total_events > 0 else 0
                click.echo(f"  {event_type}: {count:,} ({pct:.1f}%)")

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
        click.echo("  Verified tables:")
    else:
        click.secho(f"✓ Created new database at {db_path}", fg="green", bold=True)
        click.echo("  Initialized tables:")

    for table_name in table_names:
        click.echo(f"    - {table_name}")

    if db_existed:
        click.echo("  No changes needed - all tables exist")

    return None


@db.command("stats")
@click.option("--db", type=click.Path(), help="Database path")
def db_stats(db: str | None) -> None:
    """Show database statistics."""
    import os

    from sqlalchemy import text

    from snore.constants import DEFAULT_DATABASE_PATH
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
        db_path = Path(db)
    else:
        init_database()
        db_path = Path(DEFAULT_DATABASE_PATH)

    with session_scope() as session:
        profile_count = session.query(models.Profile).count()
        device_count = session.query(models.Device).count()
        session_count = session.query(models.Session).count()
        day_count = session.query(models.Day).count()
        event_count = session.execute(text("SELECT COUNT(*) FROM events")).scalar()
        waveform_count = session.query(models.Waveform).count()
        analysis_count = session.query(models.AnalysisResult).count()
        pattern_count = session.query(models.DetectedPattern).count()

        sessions_with_waveforms = (
            session.query(models.Session)
            .filter(models.Session.has_waveform_data == True)
            .count()
        )
        sessions_with_events = (
            session.query(models.Session)
            .filter(models.Session.has_event_data == True)
            .count()
        )

        first_session = session.execute(
            text("SELECT MIN(start_time) as first FROM sessions")
        ).scalar()

        last_session = session.execute(
            text("SELECT MAX(start_time) as last FROM sessions")
        ).scalar()

        size_bytes = os.path.getsize(db_path) if db_path.exists() else 0
        size_mb = size_bytes / (1024 * 1024)

        click.echo("\n📊 Database Statistics")
        click.echo(f"{'=' * 50}")
        click.echo(f"Database: {db_path}")
        click.echo(f"Size: {size_mb:.1f} MB")

        click.echo("\nRow Counts")
        click.echo(f"  Profiles: {profile_count}")
        click.echo(f"  Devices: {device_count}")
        click.echo(f"  Sessions: {session_count}")
        click.echo(f"  Days: {day_count}")
        click.echo(f"  Events: {event_count}")
        click.echo(f"  Waveforms: {waveform_count}")
        click.echo(f"  Analysis Results: {analysis_count}")
        click.echo(f"  Detected Patterns: {pattern_count}")

        click.echo("\nData Coverage")
        wf_pct = (
            (sessions_with_waveforms / session_count * 100) if session_count > 0 else 0
        )
        ev_pct = (
            (sessions_with_events / session_count * 100) if session_count > 0 else 0
        )
        an_pct = (analysis_count / session_count * 100) if session_count > 0 else 0
        click.echo(
            f"  Sessions with waveforms: {sessions_with_waveforms}/{session_count} ({wf_pct:.1f}%)"
        )
        click.echo(
            f"  Sessions with events: {sessions_with_events}/{session_count} ({ev_pct:.1f}%)"
        )
        click.echo(
            f"  Sessions analyzed: {analysis_count}/{session_count} ({an_pct:.1f}%)"
        )

        if first_session and last_session:
            first_dt = (
                datetime.fromisoformat(first_session)
                if isinstance(first_session, str)
                else first_session
            )
            last_dt = (
                datetime.fromisoformat(last_session)
                if isinstance(last_session, str)
                else last_session
            )
            click.echo(f"\nDate range: {first_dt:%Y-%m-%d} to {last_dt:%Y-%m-%d}")

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
    import os

    from sqlalchemy import text

    from snore.constants import DEFAULT_DATABASE_PATH
    from snore.database import models
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
            device_count = session.query(models.Device).count()
            session_count = session.query(models.Session).count()
            event_count = session.execute(text("SELECT COUNT(*) FROM events")).scalar()

            first_session = session.execute(
                text("SELECT MIN(start_time) as first FROM sessions")
            ).scalar()

            last_session = session.execute(
                text("SELECT MAX(start_time) as last FROM sessions")
            ).scalar()

            size_bytes = os.path.getsize(db_path) if db_path.exists() else 0
            size_gb = size_bytes / (1024 * 1024 * 1024)

            click.echo(f"\nDatabase: {db_path}")
            click.echo(f"Size: {size_gb:.1f} GB")
            click.echo(f"Devices: {device_count}")
            click.echo(f"Sessions: {session_count}")
            click.echo(f"Events: {event_count:,}")

            if first_session and last_session:
                first_dt = (
                    datetime.fromisoformat(first_session)
                    if isinstance(first_session, str)
                    else first_session
                )
                last_dt = (
                    datetime.fromisoformat(last_session)
                    if isinstance(last_session, str)
                    else last_session
                )
                click.echo(f"Date range: {first_dt:%Y-%m-%d} to {last_dt:%Y-%m-%d}")

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
def profile() -> None:
    """Profile management commands."""
    pass


@profile.command("list")
@click.option("--db", type=click.Path(), help="Database path")
def profile_list(db: str | None) -> None:
    """List all profiles in the database."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        profiles = session.query(models.Profile).all()

        if not profiles:
            click.echo("No profiles found in database")
            return

        default_profile = get_default_profile()
        click.echo("\nProfiles:\n")

        for prof in profiles:
            is_default = prof.username == default_profile
            prefix = "* " if is_default else "  "
            click.echo(f"{prefix}{prof.username}")

            if prof.first_name or prof.last_name:
                name_parts = [prof.first_name, prof.last_name]
                full_name = " ".join(part for part in name_parts if part)
                click.echo(f"    Name: {full_name}")

            session_count = (
                session.query(models.Session)
                .join(models.Day)
                .filter(models.Day.profile_id == prof.id)
                .count()
            )

            day_count = (
                session.query(models.Day)
                .filter(models.Day.profile_id == prof.id)
                .count()
            )

            click.echo(f"    Sessions: {session_count}")
            click.echo(f"    Days with data: {day_count}")

            if day_count > 0:
                days = (
                    session.query(models.Day)
                    .filter(models.Day.profile_id == prof.id)
                    .order_by(models.Day.date)
                    .all()
                )
                first_date = days[0].date
                last_date = days[-1].date
                click.echo(f"    Date range: {first_date} to {last_date}")

            click.echo()

        if default_profile:
            click.echo(f"Default profile: {default_profile} (marked with *)")


@profile.command("show")
@click.argument("username")
@click.option("--db", type=click.Path(), help="Database path")
def profile_show(username: str, db: str | None) -> None:
    """Show details for a specific profile."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=username).first()

        if not prof:
            click.echo(f"Error: Profile '{username}' not found", err=True)
            sys.exit(1)

        default_profile = get_default_profile()
        is_default = prof.username == default_profile

        click.echo(f"\nProfile: {prof.username}")
        if is_default:
            click.echo("  (default profile)")

        if prof.first_name or prof.last_name:
            name_parts = [prof.first_name, prof.last_name]
            full_name = " ".join(part for part in name_parts if part)
            click.echo(f"  Name: {full_name}")

        if prof.date_of_birth:
            click.echo(f"  Date of Birth: {prof.date_of_birth}")

        if prof.height_cm:
            click.echo(f"  Height: {prof.height_cm} cm")

        click.echo(f"  Created: {prof.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

        session_count = (
            session.query(models.Session)
            .join(models.Day)
            .filter(models.Day.profile_id == prof.id)
            .count()
        )

        day_count = (
            session.query(models.Day).filter(models.Day.profile_id == prof.id).count()
        )

        analysis_count = (
            session.query(models.AnalysisResult)
            .join(models.Session)
            .join(models.Day)
            .filter(models.Day.profile_id == prof.id)
            .count()
        )

        click.echo(f"\n  Sessions: {session_count}")
        click.echo(f"  Days with data: {day_count}")
        click.echo(f"  Analysis results: {analysis_count}")

        if day_count > 0:
            days = (
                session.query(models.Day)
                .filter(models.Day.profile_id == prof.id)
                .order_by(models.Day.date)
                .all()
            )
            first_date = days[0].date
            last_date = days[-1].date
            click.echo(f"  Date range: {first_date} to {last_date}")

        click.echo()


@profile.command("create")
@click.argument("username")
@click.option("--first-name", help="First name")
@click.option("--last-name", help="Last name")
@click.option("--db", type=click.Path(), help="Database path")
def profile_create(
    username: str, first_name: str | None, last_name: str | None, db: str | None
) -> None:
    """Create a new profile."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        existing = session.query(models.Profile).filter_by(username=username).first()
        if existing:
            click.echo(f"Error: Profile '{username}' already exists", err=True)
            sys.exit(1)

        profile = models.Profile(
            username=username,
            first_name=first_name,
            last_name=last_name,
            settings={"day_split_time": "12:00:00"},
        )
        session.add(profile)
        session.commit()

        click.echo(f"✓ Created profile: {username}")
        if first_name or last_name:
            name_parts = [first_name, last_name]
            full_name = " ".join(part for part in name_parts if part)
            click.echo(f"  Name: {full_name}")


@profile.command("delete")
@click.argument("username")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option("--dry-run", is_flag=True, help="Preview what would be deleted")
@click.option("--db", type=click.Path(), help="Database path")
def profile_delete(username: str, force: bool, dry_run: bool, db: str | None) -> None:
    """Delete a profile and all associated data (cascade delete)."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=username).first()

        if not prof:
            click.echo(f"Error: Profile '{username}' not found", err=True)
            sys.exit(1)

        day_count = (
            session.query(models.Day).filter(models.Day.profile_id == prof.id).count()
        )

        session_count = (
            session.query(models.Session)
            .join(models.Day)
            .filter(models.Day.profile_id == prof.id)
            .count()
        )

        analysis_count = (
            session.query(models.AnalysisResult)
            .join(models.Session)
            .join(models.Day)
            .filter(models.Day.profile_id == prof.id)
            .count()
        )

        device_count = (
            session.query(models.Device)
            .filter(models.Device.profile_id == prof.id)
            .count()
        )

        click.echo(f"\nProfile: {username}")
        click.echo(f"  Days: {day_count}")
        click.echo(f"  Sessions: {session_count}")
        click.echo(f"  Devices: {device_count}")
        click.echo(f"  Analysis results: {analysis_count}")

        if dry_run:
            click.echo("\n[DRY RUN] No data was deleted")
            return

        if not force:
            click.echo(
                "\n⚠️  WARNING: This will permanently delete all data for this profile!"
            )
            if not click.confirm(
                f"Are you sure you want to delete profile '{username}'?", default=False
            ):
                click.echo("Deletion cancelled")
                return

        default_profile = get_default_profile()
        if default_profile == username:
            unset_default_profile()
            click.echo(f"✓ Unset default profile: {username}")

        session.delete(prof)
        session.commit()

        click.echo(f"\n✓ Deleted profile: {username}")
        click.echo(
            f"  Cascade deleted: {day_count} days, {session_count} sessions, {device_count} devices, {analysis_count} analyses"
        )


@profile.command("set-default")
@click.argument("username")
@click.option("--db", type=click.Path(), help="Database path")
def profile_set_default(username: str, db: str | None) -> None:
    """Set the default profile for CLI commands."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=username).first()
        if not prof:
            all_profiles = session.query(models.Profile).all()
            if all_profiles:
                available = ", ".join([p.username for p in all_profiles])
                click.echo(f"Error: Profile '{username}' not found", err=True)
                click.echo(f"Available profiles: {available}", err=True)
            else:
                click.echo("Error: No profiles in database", err=True)
                click.echo("Import data first: snore import <path>", err=True)
            sys.exit(1)

    set_default_profile(username)
    click.echo(f"✓ Default profile: {username}")
    click.echo(f"  Config: {get_config_path()}")


@profile.command("unset-default")
def profile_unset_default() -> None:
    """Remove the default profile setting."""
    from snore.config import get_config_path, unset_default_profile

    unset_default_profile()
    click.echo("✓ Default profile unset")
    click.echo(f"  Config: {get_config_path()}")


@profile.command("show-default")
def profile_show_default() -> None:
    """Show current default profile."""
    default = get_default_profile()
    if default:
        click.echo(f"Default profile: {default}")
        click.echo(f"  Config: {get_config_path()}")
    else:
        click.echo("No default profile set")
        click.echo("  Set with: snore profile set-default <name>")


@cli.group()
def session() -> None:
    """Session management commands."""
    pass


@session.command("list")
@click.option("--profile", "-p", help="Filter by profile username")
@click.option(
    "--all-profiles", is_flag=True, help="Include all profiles (ignores --profile)"
)
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
    type=click.Choice(["date-asc", "date-desc", "profile", "session-id", "duration"]),
    default="date-desc",
    help="Sort order for results (default: date-desc)",
)
@click.option("--db", type=click.Path(), help="Database path")
def session_list(
    profile: str | None,
    all_profiles: bool,
    from_date: datetime | None,
    to_date: datetime | None,
    limit: int,
    sort_by: str,
    db: str | None,
) -> None:
    """List imported sessions."""
    from sqlalchemy import text

    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    effective_profile = None
    using_default = False

    if not all_profiles:
        if profile:
            effective_profile = profile
        else:
            default = get_default_profile()
            if default:
                effective_profile = default
                using_default = True

    with session_scope() as db_session:
        where_clause = "WHERE 1=1"
        params: dict[str, Any] = {}

        if effective_profile:
            where_clause += " AND profiles.username = :profile"
            params["profile"] = effective_profile

        if from_date:
            where_clause += " AND sessions.start_time >= :from_date"
            params["from_date"] = from_date

        if to_date:
            where_clause += " AND sessions.start_time <= :to_date"
            params["to_date"] = to_date

        count_query = f"""
            SELECT COUNT(*)
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            JOIN days ON sessions.day_id = days.id
            JOIN profiles ON days.profile_id = profiles.id
            {where_clause}
        """

        total_count = db_session.execute(text(count_query), params).scalar()

        sort_clauses = {
            "date-asc": "sessions.start_time ASC",
            "date-desc": "sessions.start_time DESC",
            "profile": "profiles.username ASC, sessions.start_time DESC",
            "session-id": "sessions.id ASC",
            "duration": "sessions.duration_seconds DESC",
        }
        order_by = sort_clauses.get(sort_by, "sessions.start_time DESC")

        list_query = f"""
            SELECT
                sessions.id,
                sessions.start_time,
                sessions.duration_seconds,
                devices.manufacturer,
                devices.model,
                profiles.username,
                statistics.ahi
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            JOIN days ON sessions.day_id = days.id
            JOIN profiles ON days.profile_id = profiles.id
            LEFT JOIN statistics ON sessions.id = statistics.session_id
            {where_clause}
            ORDER BY {order_by}
        """

        if limit > 0:
            list_query += f" LIMIT {limit}"

        result = db_session.execute(text(list_query), params)
        sessions = result.fetchall()

        if not sessions:
            click.echo("No sessions found")
            return

        if using_default:
            click.echo(f"(Using default profile: {effective_profile})\n")

        click.echo(
            f"{'ID':<5} {'Date':<12} {'Time':<8} {'Duration':<10} {'Profile':<15} {'Device':<25} {'AHI':<8}"
        )
        click.echo("-" * 95)

        for sess in sessions:
            start = (
                datetime.fromisoformat(sess.start_time)
                if isinstance(sess.start_time, str)
                else sess.start_time
            )
            duration_hours = (
                sess.duration_seconds / 3600 if sess.duration_seconds else 0
            )
            device_name = f"{sess.manufacturer} {sess.model}"
            ahi_str = f"{sess.ahi:.1f}" if sess.ahi is not None else "N/A"

            click.echo(
                f"{sess.id:<5} "
                f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
                f"{duration_hours:>6.1f}h    "
                f"{sess.username:<15} "
                f"{device_name:<25} "
                f"{ahi_str:<8}"
            )

        if total_count is not None and limit > 0 and total_count > limit:
            click.echo(f"\nShowing {len(sessions)} of {total_count} sessions")
            click.echo(f"Tip: Use '--limit {total_count}' or '--limit 0' to show all")
        else:
            click.echo(f"\nShowing all {len(sessions)} sessions")


@session.command("show")
@click.argument("session_id", type=int)
@click.option("--db", type=click.Path(), help="Database path")
def session_show(session_id: int, db: str | None) -> None:
    """Show details for a specific session."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        sess = (
            db_session.query(models.Session)
            .filter(models.Session.id == session_id)
            .first()
        )

        if not sess:
            click.echo(f"Error: Session {session_id} not found", err=True)
            sys.exit(1)

        device = (
            db_session.query(models.Device)
            .filter(models.Device.id == sess.device_id)
            .first()
        )
        day = db_session.query(models.Day).filter(models.Day.id == sess.day_id).first()
        profile = (
            db_session.query(models.Profile)
            .filter(models.Profile.id == day.profile_id)
            .first()
            if day
            else None
        )
        stats = (
            db_session.query(models.Statistics)
            .filter(models.Statistics.session_id == sess.id)
            .first()
        )
        event_count = (
            db_session.query(models.Event)
            .filter(models.Event.session_id == sess.id)
            .count()
        )
        waveform_count = (
            db_session.query(models.Waveform)
            .filter(models.Waveform.session_id == sess.id)
            .count()
        )

        click.echo(f"\nSession ID: {sess.id}")
        click.echo(f"  Device Session ID: {sess.device_session_id}")

        if profile:
            click.echo(f"  Profile: {profile.username}")

        if device:
            click.echo(
                f"  Device: {device.manufacturer} {device.model} (SN: {device.serial_number})"
            )

        click.echo(f"  Start: {sess.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"  End: {sess.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        duration_hours = sess.duration_seconds / 3600 if sess.duration_seconds else 0
        click.echo(f"  Duration: {duration_hours:.2f}h ({sess.duration_seconds}s)")

        if sess.therapy_mode:
            click.echo(f"  Therapy Mode: {sess.therapy_mode}")

        click.echo("\n  Data:")
        click.echo(f"    Events: {event_count}")
        click.echo(f"    Waveforms: {waveform_count}")
        click.echo(f"    Has Statistics: {sess.has_statistics}")
        click.echo(f"    Has Event Data: {sess.has_event_data}")

        if stats:
            click.echo("\n  Statistics:")
            if stats.ahi is not None:
                click.echo(f"    AHI: {stats.ahi:.1f}")
            if stats.usage_hours is not None:
                click.echo(f"    Usage: {stats.usage_hours:.1f}h")
            if stats.leak_percentile_70 is not None:
                click.echo(f"    Leak (70th): {stats.leak_percentile_70:.1f} L/min")

        click.echo()


@session.command("delete")
@click.option("--profile", "-p", help="Filter by profile username")
@click.option(
    "--all-profiles", is_flag=True, help="Include all profiles (ignores --profile)"
)
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
    profile: str | None,
    all_profiles: bool,
    session_ids: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
    delete_all: bool,
    dry_run: bool,
    force: bool,
    db: str | None,
) -> int | None:
    """Delete sessions from the database."""
    from sqlalchemy import bindparam, text

    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    if not any([profile, all_profiles, session_ids, from_date, to_date, delete_all]):
        click.echo("❌ Error: You must specify at least one filter:", err=True)
        click.echo("  • --profile <username>")
        click.echo("  • --all-profiles")
        click.echo("  • --session-id <ids>")
        click.echo("  • --from <date>")
        click.echo("  • --to <date>")
        click.echo("  • --all")
        return 1

    with session_scope() as db_session:
        query = """
            SELECT
                sessions.id,
                sessions.device_session_id,
                sessions.start_time,
                sessions.duration_seconds,
                devices.manufacturer,
                devices.model,
                devices.serial_number,
                profiles.username
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            JOIN days ON sessions.day_id = days.id
            JOIN profiles ON days.profile_id = profiles.id
            WHERE 1=1
        """
        params: dict[str, Any] = {}

        if not all_profiles and profile:
            query += " AND profiles.username = :profile"
            params["profile"] = profile

        if session_ids:
            try:
                id_list = [int(sid.strip()) for sid in session_ids.split(",")]
                query += " AND sessions.id IN :session_ids"
                params["session_ids"] = id_list
            except ValueError:
                click.echo(
                    "❌ Error: Invalid session ID format. Use comma-separated integers (e.g., '1,2,3')",
                    err=True,
                )
                return 1

        if from_date:
            query += " AND sessions.start_time >= :from_date"
            params["from_date"] = from_date

        if to_date:
            query += " AND sessions.start_time <= :to_date"
            params["to_date"] = to_date

        query += " ORDER BY sessions.start_time DESC"

        if "session_ids" in params:
            result = db_session.execute(
                text(query).bindparams(bindparam("session_ids", expanding=True)), params
            )
        else:
            result = db_session.execute(text(query), params)
        sessions = result.fetchall()

        if not sessions:
            click.echo("⚠️  No sessions found matching the specified criteria")
            return 0

        session_ids_to_delete = [s.id for s in sessions]

        event_count = db_session.execute(
            text(
                "SELECT COUNT(*) as count FROM events WHERE session_id IN :session_ids"
            ).bindparams(bindparam("session_ids", expanding=True)),
            {"session_ids": session_ids_to_delete},
        ).scalar()

        waveform_count = db_session.execute(
            text(
                "SELECT COUNT(*) as count FROM waveforms WHERE session_id IN :session_ids"
            ).bindparams(bindparam("session_ids", expanding=True)),
            {"session_ids": session_ids_to_delete},
        ).scalar()

        stats_count = db_session.execute(
            text(
                "SELECT COUNT(*) as count FROM statistics WHERE session_id IN :session_ids"
            ).bindparams(bindparam("session_ids", expanding=True)),
            {"session_ids": session_ids_to_delete},
        ).scalar()

        click.echo(f"\n{'=' * 80}")
        if dry_run:
            click.echo("🔍 DRY RUN MODE - No data will be deleted")
        else:
            click.echo("⚠️  Sessions to be DELETED")
        click.echo(f"{'=' * 80}\n")

        click.echo(
            f"{'ID':<5} {'Date':<12} {'Time':<8} {'Duration':<10} {'Profile':<15} {'Device':<25}"
        )
        click.echo("-" * 80)

        for sess in sessions:
            start = (
                datetime.fromisoformat(sess.start_time)
                if isinstance(sess.start_time, str)
                else sess.start_time
            )
            duration_hours = (
                sess.duration_seconds / 3600 if sess.duration_seconds else 0
            )
            device_name = f"{sess.manufacturer} {sess.model}"

            click.echo(
                f"{sess.id:<5} "
                f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
                f"{duration_hours:>6.1f}h    "
                f"{sess.username:<15} "
                f"{device_name:<25}"
            )

        click.echo("\n" + "=" * 80)
        click.echo("📊 Deletion Summary")
        click.echo("=" * 80)
        click.echo(f"Sessions:    {len(sessions)}")
        click.echo(f"Events:      {event_count}")
        click.echo(f"Waveforms:   {waveform_count}")
        click.echo(f"Statistics:  {stats_count}")
        click.echo("=" * 80 + "\n")

        if dry_run:
            click.echo("✓ Dry run complete. Use without --dry-run to delete.")
            return 0

        if not force:
            click.echo("⚠️  WARNING: This action cannot be undone!")
            if not click.confirm("Are you sure you want to delete these sessions?"):
                click.echo("Deletion cancelled")
                return 0

        db_session.execute(
            text("DELETE FROM sessions WHERE id IN :session_ids").bindparams(
                bindparam("session_ids", expanding=True)
            ),
            {"session_ids": session_ids_to_delete},
        )
        db_session.commit()

        click.echo(
            f"\n✓ Successfully deleted {len(sessions)} session(s) and related data"
        )

        if len(sessions) > 10:
            click.echo("\n💡 Tip: Run 'snore db vacuum' to reclaim disk space")

        return 0


@cli.group()
def config() -> None:
    """Configuration management commands."""
    pass


@config.command("show")
def show_config_cmd() -> None:
    """Show all configuration settings."""
    from snore.config import load_config

    config_path = get_config_path()
    if not config_path.exists():
        click.echo(f"No config file: {config_path}")
        return

    click.echo(f"Config file: {config_path}\n")
    config_data = load_config()
    if not config_data:
        click.echo("Configuration is empty.")
        return

    click.echo("Settings:")
    if "profile" in config_data:
        click.echo("  [profile]")
        for key, value in config_data["profile"].items():
            click.echo(f'    {key} = "{value}"')


@cli.group()
def analysis() -> None:
    """Analyze CPAP sessions and view results."""
    pass


@analysis.command("run")
@click.option(
    "--profile", required=False, help="Profile username (optional if default set)"
)
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
    profile: str | None,
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
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    if session_id is not None and profile is None:
        resolved_profile = None
    else:
        with session_scope() as temp_session:
            resolved_profile = resolve_profile(profile, temp_session)

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
        prof = None
        if resolved_profile is not None:
            prof = (
                session.query(models.Profile)
                .filter_by(username=resolved_profile)
                .first()
            )
            if not prof:
                click.echo(f"Error: Profile '{resolved_profile}' not found", err=True)
                sys.exit(1)

        if single_count > 0:
            if date is not None and prof is None:
                click.echo(
                    "Error: --date requires a profile. Use --session-id instead.",
                    err=True,
                )
                sys.exit(1)
            _analyze_single_session(
                session,
                prof,
                session_id,
                date,
                no_store,
                debug_events,
                mode,
                all_modes,
                plain,
            )
        else:
            if prof is None:
                click.echo("Error: Batch analysis requires a profile", err=True)
                sys.exit(1)
            _analyze_batch(
                session,
                prof,
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
    "--profile", required=False, help="Profile username (optional if default set)"
)
@click.option(
    "--all-profiles", is_flag=True, help="Include all profiles (ignores --profile)"
)
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
    type=click.Choice(["date-asc", "date-desc", "profile", "session-id"]),
    default="date-desc",
    help="Sort order for results (default: date-desc)",
)
@click.option("--db", type=click.Path(), help="Database path")
def list_cmd(
    profile: str | None,
    all_profiles: bool,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str,
    db: str | None,
) -> None:
    """List sessions with analysis status."""
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    if all_profiles:
        profile_id = None
    else:
        with session_scope() as temp_session:
            resolved_profile = resolve_profile(profile, temp_session)

        with session_scope() as session:
            prof = (
                session.query(models.Profile)
                .filter_by(username=resolved_profile)
                .first()
            )
            if not prof:
                click.echo(f"Error: Profile '{resolved_profile}' not found", err=True)
                sys.exit(1)
            profile_id = prof.id

    with session_scope() as session:
        _list_sessions(session, profile_id, start, end, limit, analyzed_only, sort_by)


@analysis.command("show")
@click.option(
    "--profile", required=False, help="Profile username (optional if default set)"
)
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
    profile: str | None,
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

    if session_id is not None and profile is None:
        resolved_profile = None
    else:
        with session_scope() as temp_session:
            resolved_profile = resolve_profile(profile, temp_session)

    with session_scope() as session:
        prof = None
        if resolved_profile is not None:
            prof = (
                session.query(models.Profile)
                .filter_by(username=resolved_profile)
                .first()
            )
            if not prof:
                click.echo(f"Error: Profile '{resolved_profile}' not found", err=True)
                sys.exit(1)

        if date is not None:
            if prof is None:
                click.echo(
                    "Error: --date requires a profile. Use --session-id instead.",
                    err=True,
                )
                sys.exit(1)

            db_session = (
                session.query(models.Session)
                .join(models.Day)
                .filter(
                    models.Day.profile_id == prof.id, models.Day.date == date.date()
                )
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
                    _format_time_offset,
                )
                con.print(fn_text)

            if validation["false_positives"]:
                fp_text = format_event_list(
                    validation["false_positives"],
                    "  Extra events",
                    _format_time_offset,
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
            .filter(models.Day.profile_id == prof.id, models.Day.date == date.date())
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

    query = (
        session.query(models.Session)
        .join(models.Day)
        .filter(models.Day.profile_id == prof.id)
    )

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
    profile_id: int | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str = "date-desc",
) -> None:
    """List sessions and their analysis status.

    Args:
        profile_id: Profile ID to filter by, or None to show all profiles
        limit: Maximum sessions to show (0 for unlimited)
        sort_by: Sort order (date-asc, date-desc, profile, session-id, ahi)
    """
    from sqlalchemy.orm import joinedload

    from snore.database import models

    query = session.query(models.Session).join(models.Day)

    query = query.options(joinedload(models.Session.day).joinedload(models.Day.profile))

    if profile_id is None or sort_by == "profile":
        query = query.join(models.Profile, models.Day.profile_id == models.Profile.id)

    if profile_id is not None:
        query = query.filter(models.Day.profile_id == profile_id)

    if start:
        query = query.filter(models.Day.date >= start.date())
    if end:
        query = query.filter(models.Day.date <= end.date())

    sort_clauses = {
        "date-asc": models.Day.date.asc(),
        "date-desc": models.Day.date.desc(),
        "profile": (models.Profile.username.asc(), models.Day.date.desc()),
        "session-id": models.Session.id.asc(),
    }

    sort_clause = sort_clauses.get(sort_by, models.Day.date.desc())
    if isinstance(sort_clause, tuple):
        query = query.order_by(*sort_clause)
    else:
        query = query.order_by(sort_clause)

    total_sessions = query.count()

    if limit > 0:
        query = query.limit(limit)

    sessions = query.all()

    if not sessions:
        click.echo("No sessions found")
        return

    if profile_id is None:
        click.echo(
            f"{'Date':<12} {'Profile':<12} {'ID':<6} {'Duration':<10} {'Analyzed':<10} {'Analysis ID':<12}"
        )
        click.echo("-" * 72)
    else:
        click.echo(
            f"{'Date':<12} {'ID':<6} {'Duration':<10} {'Analyzed':<10} {'Analysis ID':<12}"
        )
        click.echo("-" * 60)

    displayed_count = 0

    for db_session in sessions:
        analysis = (
            session.query(models.AnalysisResult)
            .filter_by(session_id=db_session.id)
            .order_by(models.AnalysisResult.created_at.desc())
            .first()
        )

        has_analysis = analysis is not None

        if analyzed_only and not has_analysis:
            continue

        displayed_count += 1

        duration = (
            f"{db_session.duration_seconds / 3600:.1f}h"
            if db_session.duration_seconds
            else "N/A"
        )
        analyzed_str = "✓" if has_analysis else "✗"
        analysis_id_str = str(analysis.id) if analysis else "-"

        day_date = (
            db_session.day.date if db_session.day else db_session.start_time.date()
        )
        if profile_id is None:
            profile_name = db_session.day.profile.username if db_session.day else "N/A"
            click.echo(
                f"{day_date!s:<12} {profile_name:<12} {db_session.id:<6} {duration:<10} "
                f"{analyzed_str:<10} {analysis_id_str:<12}"
            )
        else:
            click.echo(
                f"{day_date!s:<12} {db_session.id:<6} {duration:<10} "
                f"{analyzed_str:<10} {analysis_id_str:<12}"
            )

    if analyzed_only and displayed_count > 0:
        click.echo(f"\nShowing {displayed_count} analyzed session(s)")
    elif limit > 0 and total_sessions > limit:
        click.echo(
            f"\nShowing {limit} of {total_sessions} sessions (most recent first)"
        )
        click.echo(
            "Tip: Use --limit <number> to see more sessions, or --limit 0 to see all"
        )
    elif limit == 0 and total_sessions > DEFAULT_LIST_SESSIONS_LIMIT:
        click.echo(f"\nShowing all {total_sessions} sessions")


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
    from sqlalchemy import bindparam, text

    from snore.database.session import init_database, session_scope

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

    with session_scope() as session:
        query = """
            SELECT DISTINCT
                sessions.id,
                sessions.device_session_id,
                sessions.start_time,
                devices.manufacturer,
                devices.model
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            JOIN analysis_results ON sessions.id = analysis_results.session_id
            WHERE 1=1
        """
        params: dict[str, Any] = {}

        if session_ids:
            try:
                id_list = [int(sid.strip()) for sid in session_ids.split(",")]
                query += " AND sessions.id IN :session_ids"
                params["session_ids"] = id_list
            except ValueError:
                click.echo(
                    "❌ Error: Invalid session ID format. Use comma-separated integers (e.g., '1,2,3')",
                    err=True,
                )
                return 1

        if from_date:
            query += " AND sessions.start_time >= :from_date"
            params["from_date"] = from_date

        if to_date:
            query += " AND sessions.start_time <= :to_date"
            params["to_date"] = to_date

        query += " ORDER BY sessions.start_time DESC"

        if "session_ids" in params:
            result = session.execute(
                text(query).bindparams(bindparam("session_ids", expanding=True)), params
            )
        else:
            result = session.execute(text(query), params)
        sessions_with_analysis = result.fetchall()

        if not sessions_with_analysis:
            click.echo(
                "⚠️  No sessions with analysis results found matching the specified criteria"
            )
            return 0

        session_ids_list = [s.id for s in sessions_with_analysis]

        analysis_counts = session.execute(
            text(
                """
                SELECT session_id, COUNT(*) as count
                FROM analysis_results
                WHERE session_id IN :session_ids
                GROUP BY session_id
            """
            ).bindparams(bindparam("session_ids", expanding=True)),
            {"session_ids": session_ids_list},
        ).fetchall()

        analysis_count_dict = {row[0]: int(row[1]) for row in analysis_counts}

        total_analysis_records = sum(analysis_count_dict.values())
        records_to_delete = (
            total_analysis_records if all_versions else len(sessions_with_analysis)
        )

        patterns_count = session.execute(
            text(
                """
                SELECT COUNT(*) as count
                FROM detected_patterns
                WHERE analysis_result_id IN (
                    SELECT id FROM analysis_results WHERE session_id IN :session_ids
                )
            """
            ).bindparams(bindparam("session_ids", expanding=True)),
            {"session_ids": session_ids_list},
        ).scalar()

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

        for sess in sessions_with_analysis:
            start = (
                datetime.fromisoformat(sess.start_time)
                if isinstance(sess.start_time, str)
                else sess.start_time
            )
            device_name = f"{sess.manufacturer} {sess.model}"
            version_count = analysis_count_dict.get(sess.id, 0)

            click.echo(
                f"{sess.id:<8} "
                f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
                f"{version_count:<10} "
                f"{device_name:<25}"
            )

        click.echo("\n" + "=" * 80)
        click.echo("📊 Deletion Summary")
        click.echo("=" * 80)
        click.echo(f"Sessions with analysis:          {len(sessions_with_analysis)}")
        click.echo(
            f"Total analysis records:          {total_analysis_records}"
            + (
                " (all versions)"
                if all_versions or total_analysis_records == len(sessions_with_analysis)
                else ""
            )
        )
        click.echo(
            f"Analysis records to delete:      {records_to_delete}"
            + (
                " (latest only)"
                if not all_versions
                and total_analysis_records > len(sessions_with_analysis)
                else ""
            )
        )
        click.echo(
            f"Detected patterns to delete:     {patterns_count} (cascade delete)"
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

        if all_versions:
            session.execute(
                text(
                    "DELETE FROM analysis_results WHERE session_id IN :session_ids"
                ).bindparams(bindparam("session_ids", expanding=True)),
                {"session_ids": session_ids_list},
            )
            deleted_count = records_to_delete
        else:
            deleted_count = 0
            for session_id in session_ids_list:
                latest_result = session.execute(
                    text(
                        """
                        SELECT id FROM analysis_results
                        WHERE session_id = :session_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    """
                    ),
                    {"session_id": session_id},
                ).fetchone()

                if latest_result:
                    session.execute(
                        text("DELETE FROM analysis_results WHERE id = :analysis_id"),
                        {"analysis_id": latest_result.id},
                    )
                    deleted_count += 1

        session.commit()

        click.echo(
            f"\n✓ Successfully deleted {deleted_count} analysis record(s) for {len(sessions_with_analysis)} session(s)"
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
        click.echo("  snore completions install")
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
        click.echo("  snore completions install")
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
    "--profile",
    type=str,
    help="Profile username (optional if default set)",
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
    profile: str | None,
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

    with session_scope() as temp_session:
        profile = resolve_profile(profile, temp_session)

    with session_scope() as db_session:
        try:
            validator = BatchValidator(db_session, profile)

            click.echo(
                f"Running validation from {date_from.date()} to {date_to.date()}..."
            )
            click.echo(f"Profile: {profile}")
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
    "--profile",
    type=str,
    help="Profile username (optional if default set)",
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
    profile: str | None,
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

    with session_scope() as temp_session:
        profile = resolve_profile(profile, temp_session)

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
                        "time_into_session": _format_time_offset(time_offset),
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
                        "time_into_session": _format_time_offset(time_offset),
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
                        "time_into_session": _format_time_offset(time_offset),
                        "event_type": "H",
                        "duration_sec": f"{hypopnea.duration:.1f}",
                        "source": "programmatic",
                        "matched": "?",
                    }
                )

            export_events_list.sort(key=lambda x: x["time_into_session"])

            import bisect

            prog_times = sorted(
                _parse_time_offset(e["time_into_session"])
                for e in export_events_list
                if e["source"] == "programmatic"
            )
            machine_times = sorted(
                _parse_time_offset(e["time_into_session"])
                for e in export_events_list
                if e["source"] == "machine"
            )

            for i, event_dict in enumerate(export_events_list):
                if event_dict["source"] == "machine":
                    machine_time = _parse_time_offset(event_dict["time_into_session"])
                    idx = bisect.bisect_left(prog_times, machine_time - 5.0)
                    is_matched = any(
                        abs(machine_time - prog_times[j]) <= 5.0
                        for j in range(idx, min(idx + 10, len(prog_times)))
                    )
                    export_events_list[i]["matched"] = "yes" if is_matched else "no"
                else:
                    prog_time = _parse_time_offset(event_dict["time_into_session"])
                    idx = bisect.bisect_left(machine_times, prog_time - 5.0)
                    is_matched = any(
                        abs(prog_time - machine_times[j]) <= 5.0
                        for j in range(idx, min(idx + 10, len(machine_times)))
                    )
                    export_events_list[i]["matched"] = "yes" if is_matched else "no"

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


def _format_time_offset(seconds: float) -> str:
    """Format time offset as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_time_offset(time_str: str) -> float:
    """Parse HH:MM:SS to seconds."""
    parts = time_str.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


@cli.group()
def waveform() -> None:
    """Waveform inspection and visualization commands."""
    pass


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
@click.option("--profile", help="Profile username (optional if default set)")
@click.option("--db", type=click.Path(), help="Database path")
@click.option(
    "--mode", "-m", default="aasm", help="Detection mode to compare (default: aasm)"
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Enable interactive zoom/pan mode (vim-style h/j/k/l or arrows, q to quit)",
)
def show_waveform(
    session_id: int | None,
    date: datetime | None,
    time: str,
    window: int,
    output_format: str,
    output: str | None,
    profile: str | None,
    db: str | None,
    mode: str,
    interactive: bool,
) -> None:
    """
    Display flow waveform at a specific time.

    View the flow waveform data centered on a specific time offset to visually
    inspect detected respiratory events.

    Examples:
        snore waveform show --session-id 37 --time 05:56:22 --window 30
        snore waveform show --date 2025-10-25 --time 01:25:16 --format csv --output waveform.csv
    """
    from snore.analysis.service import AnalysisService
    from snore.database import models
    from snore.database.session import init_database, session_scope
    from snore.waveform import WaveformInspector, WaveformRenderer
    from snore.waveform.inspector import parse_time_offset

    if session_id is None and date is None:
        click.echo("Error: Either --session-id or --date must be provided", err=True)
        sys.exit(1)

    if output_format == "csv" and output is None:
        click.echo("Error: --output is required for csv format", err=True)
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
        if session_id is None:
            profile = resolve_profile(profile, db_session)

            if date is None:
                click.echo(
                    "Error: --date is required when --session-id is not provided",
                    err=True,
                )
                sys.exit(1)

            prof = db_session.query(models.Profile).filter_by(username=profile).first()
            if not prof:
                click.echo(f"Error: Profile '{profile}' not found", err=True)
                sys.exit(1)

            session = (
                db_session.query(models.Session)
                .join(models.Day)
                .filter(
                    models.Day.profile_id == prof.id,
                    models.Day.date == date.date(),
                )
                .first()
            )

            if not session:
                click.echo(f"Error: No session found for date {date.date()}", err=True)
                sys.exit(1)

            session_id = session.id

        inspector = WaveformInspector(db_session)
        try:
            timestamps, flow_values, metadata = inspector.get_window(
                session_id=session_id,
                center_seconds=center_seconds,
                window_seconds=float(window),
            )
        except Exception as e:
            click.echo(f"Error loading waveform: {e}", err=True)
            sys.exit(1)

        if len(timestamps) == 0:
            click.echo("No data in window", err=True)
            sys.exit(1)

        analysis_service = AnalysisService(db_session)
        try:
            result = analysis_service.get_analysis_result(session_id)
        except Exception:
            result = None

        machine_events = []
        programmatic_events = []

        if result:
            start_time = center_seconds - window / 2
            end_time = center_seconds + window / 2

            if result.machine_events:
                machine_events = inspector.find_events_in_window(
                    result.machine_events, start_time, end_time
                )

            if mode in result.mode_results:
                mode_result = result.mode_results[mode]
                all_prog_events = list(mode_result.apneas) + list(mode_result.hypopneas)
                programmatic_events = inspector.find_events_in_window(
                    all_prog_events, start_time, end_time
                )

        if output_format == "plot":
            renderer = WaveformRenderer(width=80, height=20, show_events=True)
            renderer.render(
                timestamps=timestamps,
                flow_values=flow_values,
                machine_events=machine_events,
                programmatic_events=programmatic_events,
                session_id=session_id,
                center_time=time,
            )

        elif output_format == "csv":
            import csv

            if output is None:
                click.echo("Error: --output is required for csv format", err=True)
                sys.exit(1)

            with open(output, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_seconds", "flow_lpm"])
                for ts, flow in zip(timestamps, flow_values, strict=True):
                    writer.writerow([f"{ts:.3f}", f"{flow:.3f}"])

            click.echo(f"Exported {len(timestamps)} samples to {output}")


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
@click.option("--profile", help="Profile username (optional if default set)")
@click.option("--db", type=click.Path(), help="Database path")
def compare_events(
    session_id: int | None,
    date: datetime | None,
    mode: str,
    show_unmatched: bool,
    profile: str | None,
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
    from snore.database import models
    from snore.database.session import init_database, session_scope

    if session_id is None and date is None:
        click.echo("Error: Either --session-id or --date must be provided", err=True)
        sys.exit(1)

    if db:
        init_database(str(Path(db)))
    else:
        init_database()

    with session_scope() as db_session:
        if session_id is None:
            profile = resolve_profile(profile, db_session)

            if date is None:
                click.echo(
                    "Error: --date is required when --session-id is not provided",
                    err=True,
                )
                sys.exit(1)

            prof = db_session.query(models.Profile).filter_by(username=profile).first()
            if not prof:
                click.echo(f"Error: Profile '{profile}' not found", err=True)
                sys.exit(1)

            session = (
                db_session.query(models.Session)
                .join(models.Day)
                .filter(
                    models.Day.profile_id == prof.id,
                    models.Day.date == date.date(),
                )
                .first()
            )

            if not session:
                click.echo(f"Error: No session found for date {date.date()}", err=True)
                sys.exit(1)

            session_id = session.id

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
                time_str = _format_time_offset(event.start_time)
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
                time_str = _format_time_offset(event.start_time)
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
                time_str = _format_time_offset(event.start_time)
                conf = getattr(event, "confidence", 0)
                flow_red = getattr(event, "flow_reduction", 0)
                click.echo(
                    f"  H at {time_str} (conf: {conf:.2f}, flow_red: {flow_red * 100:.0f}%)"
                )
                click.echo(
                    f"    → View: snore waveform show --session-id {session_id} --time {time_str}"
                )


def main() -> None:
    """Main CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
