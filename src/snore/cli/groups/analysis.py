"""Analysis command group — run, list, show, and delete CPAP analysis results."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from typing import Any

import click

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from snore.cli.decorators import (
    date_range_options,
    db_option,
    parse_id_list,
)
from snore.cli.decorators import (
    db_session as open_db_session,
)
from snore.cli.display import (
    ICON_STATS,
    console,
    err_console,
    print_dry_run_complete,
    print_dry_run_header,
    print_footer,
    print_header,
    print_kv,
    print_success,
    print_table,
    print_tip,
    print_warning,
)
from snore.cli.display.analysis import display_analysis_result
from snore.constants import DEFAULT_LIST_SESSIONS_LIMIT

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
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> int | None:
    """Run analysis on CPAP sessions."""
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

    async def _run() -> None:
        async with open_db_session(db) as session:
            if single_count > 0:
                await _analyze_single_session(
                    session,
                    session_id,
                    date,
                    no_store,
                    mode,
                    all_modes,
                    plain,
                )
            else:
                await _analyze_batch(
                    session,
                    date_from,
                    date_to,
                    date_from is None and date_to is None,
                    no_store,
                    mode,
                    all_modes,
                )

    asyncio.run(_run())
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

    async def _run() -> None:
        async with open_db_session(db) as session:
            await _list_sessions(session, date_from, date_to, limit, analyzed_only, sort_by)

    asyncio.run(_run())


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
    if session_id is None and date is None:
        raise click.ClickException("Must provide either --session-id or --date")

    if session_id is not None and date is not None:
        raise click.ClickException("--session-id and --date are mutually exclusive")

    async def _run() -> None:
        from snore.database import models
        from snore.services.analysis_facade import AnalysisFacade

        nonlocal session_id

        async with open_db_session(db) as session:
            if date is not None:
                db_session = (
                    (
                        await session.execute(
                            select(models.Session)
                            .join(models.Day)
                            .where(models.Day.date == date.date())
                        )
                    )
                    .scalars()
                    .first()
                )
                if not db_session:
                    raise click.ClickException(f"No session found for date {date.date()}")
                session_id = db_session.id

            assert session_id is not None, "session_id should not be None"

            db_session = (
                (
                    await session.execute(
                        select(models.Session)
                        .filter_by(id=session_id)
                        .options(joinedload(models.Session.day))
                    )
                )
                .scalars()
                .first()
            )
            if not db_session:
                raise click.ClickException(f"Session {session_id} not found")

            day_date = (
                db_session.day.date if db_session.day else db_session.start_time.date()
            )
            session_date_str = day_date.isoformat()

            facade = AnalysisFacade(session)
            result = await facade.get_analysis_result(session_id)

            if result is None:
                raise click.ClickException(f"No analysis found for session {session_id}")

            console.print(f"Displaying stored analysis for session {session_id}...\n")
            display_analysis_result(result, plain, session_date_str)

    asyncio.run(_run())


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

    async def _run() -> None:
        from snore.services.analysis_facade import AnalysisFacade

        async with open_db_session(db) as session:
            facade = AnalysisFacade(session)

            try:
                preview = await facade.get_delete_preview(
                    session_ids=id_list,
                    from_date=date_from,
                    to_date=date_to,
                    delete_all=delete_all,
                    all_versions=all_versions,
                )
            except ValueError as e:
                raise click.ClickException(str(e)) from e

            if preview.sessions_with_analysis == 0:
                print_warning(
                    "No sessions with analysis results found matching the specified criteria"
                )
                return

            print_footer(wide=True)
            if dry_run:
                print_dry_run_header("deleted")
            else:
                print_warning("Analysis Results to be DELETED")
            print_footer(wide=True)
            console.print()

            rows = []
            for detail in preview.session_details:
                start = detail.start_time
                if isinstance(start, str):
                    start = datetime.fromisoformat(start)
                rows.append(
                    (
                        str(detail.id),
                        f"{start:%Y-%m-%d}",
                        f"{start:%H:%M:%S}",
                        str(detail.version_count),
                        f"{detail.manufacturer} {detail.model}",
                    )
                )

            print_table(
                [
                    ("Sess ID", 8),
                    ("Date", 12),
                    ("Time", 8),
                    ("Versions", 10),
                    ("Device", 25),
                ],
                rows,
            )

            print_header("Deletion Summary", ICON_STATS, wide=True)
            console.print(
                f"Sessions with analysis:          {preview.sessions_with_analysis}"
            )
            console.print(
                f"Total analysis records:          {preview.total_analysis_records}"
                + (
                    " (all versions)"
                    if all_versions
                    or preview.total_analysis_records == preview.sessions_with_analysis
                    else ""
                )
            )
            console.print(
                f"Analysis records to delete:      {preview.records_to_delete}"
                + (
                    " (latest only)"
                    if not all_versions
                    and preview.total_analysis_records > preview.sessions_with_analysis
                    else ""
                )
            )
            console.print(
                f"Detected patterns to delete:     {preview.patterns_count} (cascade delete)"
            )
            print_footer(wide=True)
            console.print()

            if dry_run:
                print_dry_run_complete("delete")
                return

            if not force:
                print_warning(
                    "WARNING: This will delete analysis results but keep the sessions!"
                )
                if not click.confirm(
                    "Are you sure you want to delete these analysis results?"
                ):
                    console.print("Deletion cancelled")
                    return

            session_ids_to_delete = [d.id for d in preview.session_details]
            deleted_count = await facade.delete_analysis(session_ids_to_delete, all_versions)

            print_success(
                f"Successfully deleted {deleted_count} analysis record(s) for {preview.sessions_with_analysis} session(s)"
            )

            if deleted_count > 10:
                print_tip("Run 'snore db vacuum' to reclaim disk space")

    asyncio.run(_run())
    return None


async def _analyze_single_session(
    session: Any,
    session_id: int | None,
    date: datetime | None,
    no_store: bool,
    mode: tuple[str, ...],
    all_modes: bool,
    plain: bool,
) -> None:
    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.analysis.service import AnalysisService
    from snore.database import models

    if date:
        db_session = (
            (
                await session.execute(
                    select(models.Session)
                    .join(models.Day)
                    .where(models.Day.date == date.date())
                )
            )
            .scalars()
            .first()
        )
        if not db_session:
            raise click.ClickException(f"No session found for {date.date()}")
        session_id = db_session.id
        session_date_str = date.date().isoformat()
    else:
        db_session = (
            (
                await session.execute(
                    select(models.Session)
                    .filter_by(id=session_id)
                    .options(joinedload(models.Session.day))
                )
            )
            .scalars()
            .first()
        )
        if not db_session:
            raise click.ClickException(f"Session {session_id} not found")
        day_date = (
            db_session.day.date if db_session.day else db_session.start_time.date()
        )
        session_date_str = day_date.isoformat()

    console.print(f"\nAnalyzing session {session_date_str} (ID: {session_id})...")

    analysis_service = AnalysisService(session)  # TODO: AnalysisService volatile — awaiting PR-1 AsyncSession conversion

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
        err_console.print(f"\nAnalysis failed: {e}")
        logger.error("Analysis error", exc_info=True)
        raise click.Abort() from e


async def _analyze_batch(
    session: Any,
    start: datetime | None,
    end: datetime | None,
    analyze_all: bool,
    no_store: bool,
    mode: tuple[str, ...],
    all_modes: bool,
) -> None:
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

    from snore.analysis.modes import AVAILABLE_CONFIGS
    from snore.services.analysis_facade import AnalysisFacade

    modes: list[str] | None = None
    if all_modes:
        modes = list(AVAILABLE_CONFIGS.keys())
    elif mode:
        modes = list(mode)

    from_date = None if analyze_all else start
    to_date = None if analyze_all else end

    facade = AnalysisFacade(session)
    task_holder: list[Any] = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:

        def on_progress(completed: int, total: int) -> None:
            if not task_holder:
                task_holder.append(progress.add_task("Analyzing", total=total))
            progress.update(task_holder[0], advance=1)

        result = await facade.run_batch_analysis(
            from_date=from_date,
            to_date=to_date,
            modes=modes,
            store_results=not no_store,
            progress_callback=on_progress,
        )

    if result.total == 0:
        console.print("No sessions found for the specified criteria")
        return

    modes_display = modes if modes else ["aasm"]
    console.print(
        f"\nAnalyzed {result.total} sessions (modes: {', '.join(modes_display)})"
    )
    print_success("Analysis complete")
    print_kv("Successful", str(result.successful))
    print_kv("Failed", str(result.failed))
    for r in result.results:
        if not r.success and r.error:
            logger.warning(f"Failed to analyze session {r.session_id}: {r.error}")
            print_warning(f"Session {r.session_id}: {r.error}", indent=1)


async def _list_sessions(
    session: Any,
    start: datetime | None,
    end: datetime | None,
    limit: int,
    analyzed_only: bool,
    sort_by: str = "date-desc",
) -> None:
    from snore.services.analysis_facade import AnalysisFacade

    facade = AnalysisFacade(session)
    results = await facade.list_sessions_with_status(
        start=start, end=end, limit=limit, analyzed_only=analyzed_only, sort_by=sort_by
    )

    if not results:
        console.print("No sessions found")
        return

    print_table(
        [
            ("Date", 12),
            ("ID", 6),
            ("Duration", 10),
            ("Analyzed", 10),
            ("Analysis ID", 12),
        ],
        (
            (
                str(item.session_date),
                str(item.session_id),
                f"{item.duration_hours:.1f}h" if item.duration_hours else "N/A",
                "✓" if item.has_analysis else "✗",
                str(item.analysis_id) if item.analysis_id else "-",
            )
            for item in results
        ),
        wide=False,
    )

    if analyzed_only and len(results) > 0:
        console.print(f"\nShowing {len(results)} analyzed session(s)")
