"""Analysis command group — run, list, show, and delete CPAP analysis results."""

from __future__ import annotations

import logging

from datetime import datetime
from typing import TYPE_CHECKING, Any

import click

from snore.cli.decorators import date_range_options, db_option, init_db, parse_id_list
from snore.constants import DEFAULT_LIST_SESSIONS_LIMIT
from snore.utils.display import display_analysis_result

if TYPE_CHECKING:
    from snore.analysis.modes.types import ModeResult
    from snore.analysis.types import AnalysisEvent

logger = logging.getLogger(__name__)


@click.group()
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
@date_range_options
@db_option
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
    date_from: datetime | None,
    date_to: datetime | None,
    db: str | None,
    no_store: bool,
    debug_events: bool,
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> int | None:
    """Run analysis on CPAP sessions."""
    from snore.database.session import session_scope

    init_db(db)

    single_session_flags = [session_id is not None, date is not None]
    batch_flags = [date_from is not None, date_to is not None]

    single_count = sum(single_session_flags)
    batch_count = sum(batch_flags)

    if single_count > 1:
        raise click.ClickException("--session-id and --date are mutually exclusive")

    if single_count > 0 and batch_count > 0:
        raise click.ClickException(
            "Single session flags (--session-id, --date) cannot be used with batch flags (--from, --to)"
        )

    if single_count == 0 and batch_count == 0:
        raise click.ClickException(
            "Must provide at least one selection flag (--session-id, --date, --from, or --to)"
        )

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
                date_from,
                date_to,
                date_from is None and date_to is None,
                no_store,
                debug_events,
                mode,
                all_modes,
                plain,
            )

    return None


@analysis.command("list")
@date_range_options
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
@db_option
def list_cmd(
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str,
    db: str | None,
) -> None:
    """List sessions with analysis status."""
    from snore.database.session import session_scope

    init_db(db)

    with session_scope() as session:
        _list_sessions(session, date_from, date_to, limit, analyzed_only, sort_by)


@analysis.command("show")
@click.option("--session-id", type=int, help="Show analysis for session ID")
@click.option(
    "--date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Show analysis for session on date (YYYY-MM-DD)",
)
@db_option
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
    from snore.database.session import session_scope

    init_db(db)

    if session_id is None and date is None:
        raise click.ClickException("Must provide either --session-id or --date")

    if session_id is not None and date is not None:
        raise click.ClickException("--session-id and --date are mutually exclusive")

    with session_scope() as session:
        if date is not None:
            db_session = (
                session.query(models.Session)
                .join(models.Day)
                .filter(models.Day.date == date.date())
                .first()
            )
            if not db_session:
                raise click.ClickException(f"No session found for date {date.date()}")
            session_id = db_session.id

        assert session_id is not None, "session_id should not be None"

        db_session = session.query(models.Session).filter_by(id=session_id).first()
        if not db_session:
            raise click.ClickException(f"Session {session_id} not found")

        day_date = (
            db_session.day.date if db_session.day else db_session.start_time.date()
        )
        session_date_str = day_date.isoformat()

        analysis_service = AnalysisService(session)
        result = analysis_service.get_analysis_result(session_id)

        if result is None:
            raise click.ClickException(f"No analysis found for session {session_id}")

        click.echo(f"Displaying stored analysis for session {session_id}...\n")
        display_analysis_result(result, plain, session_date_str)


@analysis.command("delete")
@click.option(
    "--session-id",
    "session_ids",
    type=str,
    help="Comma-separated session IDs to delete analysis for (e.g., '1,2,3')",
)
@date_range_options
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
@db_option
def analysis_delete(
    session_ids: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    delete_all: bool,
    all_versions: bool,
    dry_run: bool,
    force: bool,
    db: str | None,
) -> int | None:
    """Delete analysis results without deleting the sessions themselves."""
    from snore.database.session import session_scope
    from snore.services.analysis_facade import AnalysisFacade

    init_db(db)

    if not any([session_ids, date_from, date_to, delete_all]):
        raise click.ClickException(
            "You must specify at least one filter:\n"
            "  • --session-id <ids>\n"
            "  • --from <date>\n"
            "  • --to <date>\n"
            "  • --all"
        )

    id_list: list[int] | None = None
    if session_ids:
        id_list = parse_id_list(session_ids)

    with session_scope() as session:
        facade = AnalysisFacade(session)

        try:
            preview = facade.get_delete_preview(
                session_ids=id_list,
                from_date=date_from,
                to_date=date_to,
                delete_all=delete_all,
                all_versions=all_versions,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e

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


def _get_validation_metrics(
    mode_result: ModeResult,
    machine_events: list[AnalysisEvent],
    mode: str,
) -> dict[str, Any]:
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
            raise click.ClickException(f"No session found for {date.date()}")
        session_id = db_session.id
        session_date_str = date.date().isoformat()
    else:
        db_session = session.query(models.Session).filter_by(id=session_id).first()
        if not db_session:
            raise click.ClickException(f"Session {session_id} not found")
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
        )
        display_analysis_result(result, plain, session_date_str)

    except Exception as e:
        click.echo(f"\nAnalysis failed: {e}", err=True)
        logger.error("Analysis error", exc_info=True)
        raise click.Abort() from e


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
