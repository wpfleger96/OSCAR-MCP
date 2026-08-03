"""import command — import CPAP data from device SD card or directory."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from pathlib import Path

import click

from rich.markup import escape

from snore.cli.decorators import actor_options, date_range_options, db_option, init_db
from snore.cli.display import (
    ICON_BACKUP,
    ICON_FILTERS,
    ICON_IMPORT,
    ICON_SCAN,
    ICON_STATS,
    console,
    print_dry_run_complete,
    print_dry_run_header,
    print_error,
    print_footer,
    print_header,
    print_info,
    print_skip,
    print_success,
    print_warning,
)
from snore.parsers.registry import parser_registry
from snore.services.import_service import ImportService


@click.command("import")
@click.argument("path", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Re-import existing sessions")
@db_option
@actor_options
@click.option("--limit", "-n", type=int, help="Limit to first N sessions")
@click.option(
    "--sort-by",
    type=click.Choice(["date-asc", "date-desc", "filesystem"]),
    default="filesystem",
    help="Session sort order (default: filesystem)",
)
@date_range_options
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
@click.option(
    "--no-backup",
    is_flag=True,
    help="Skip raw file backup (not recommended — SD card will be needed again)",
)
def import_data(
    path: str,
    force: bool,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
    limit: int | None,
    sort_by: str,
    date_from: datetime | None,
    date_to: datetime | None,
    dry_run: bool,
    no_parallel: bool,
    batch_size: int,
    select_all: bool,
    no_backup: bool,
) -> None:
    """Import CPAP data from device SD card or directory."""
    data_path = Path(path)

    init_db(db)
    service = ImportService()

    console.print(f"{ICON_SCAN} Scanning {data_path}...")
    sources = service.detect_sources(data_path)
    if not sources:
        supported = "\n".join(
            f"  - {p.manufacturer}: {p.parser_id}"
            for p in parser_registry.list_parsers()
        )
        raise click.ClickException(
            f"No compatible parser found for this data\n\nSupported devices:\n{supported}"
        )

    # Interactive source selection
    selected_sources = []
    if len(sources) > 1:
        console.print(f"\nFound {len(sources)} data sources:\n")
        for i, source in enumerate(sources, 1):
            profile = source.profile_name or "unknown"
            structure = str(source.structure_type or "unknown").replace("_", " ")
            console.print(
                f"  {i}. {escape(str(source.parser_name))} - {escape(str(profile))} ({escape(structure)})"
            )
            if source.root_path:
                console.print(f"     Path: {escape(str(source.root_path))}")

        if select_all:
            selected_sources = sources
        else:
            selection = click.prompt(
                "\nSelect sources to import (comma-separated numbers, or 'all')",
                default="all",
            )

            if selection.lower() == "all":
                selected_sources = sources
            else:
                try:
                    indices = [int(x.strip()) - 1 for x in selection.split(",")]
                    selected_sources = [
                        sources[i] for i in indices if 0 <= i < len(sources)
                    ]
                    if not selected_sources:
                        raise click.ClickException(
                            "Invalid selection: no valid indices"
                        )
                except (ValueError, IndexError):
                    raise click.ClickException(
                        f"Invalid selection: {selection}"
                    ) from None
    else:
        selected_sources = sources

    date_from_str = date_from.strftime("%Y-%m-%d") if date_from else None
    date_to_str = date_to.strftime("%Y-%m-%d") if date_to else None

    total_imported = 0
    total_skipped = 0
    total_failed = 0

    parser_map = {p.parser_id: p for p in parser_registry.list_parsers()}

    for source in selected_sources:
        # Look up the parser for display purposes (registered by detect_sources)
        parser = parser_map.get(source.parser_name)
        source_desc = source.profile_name or f"S/N {source.device_serial or 'unknown'}"

        if len(selected_sources) > 1:
            print_header(f"Processing: {escape(str(source_desc))}")

        if parser:
            print_success(
                f"Detected: {escape(str(parser.manufacturer))} ({escape(str(parser.parser_id))})"
            )
        print_info(
            f"Structure: {escape(str(source.structure_type or 'unknown').replace('_', ' '))}",
            indent=1,
        )
        if source.root_path:
            print_info(f"Data root: {escape(str(source.root_path))}", indent=1)

        if limit or date_from or date_to or sort_by != "filesystem":
            console.print(f"\n{ICON_FILTERS} Import filters:")
            if limit:
                print_info(f"• Limit: {limit} sessions", indent=1)
            if sort_by != "filesystem":
                order_desc = "oldest first" if sort_by == "date-asc" else "newest first"
                print_info(f"• Sort: {order_desc}", indent=1)
            if date_from:
                print_info(f"• From: {date_from:%Y-%m-%d}", indent=1)
            if date_to:
                print_info(f"• To: {date_to:%Y-%m-%d}", indent=1)

        cr_active = False

        def _progress(msg: str) -> None:
            nonlocal cr_active

            def overwrite(icon: str) -> None:
                console.print(f"\r{icon} {msg}   ", end="", highlight=False)

            def finish_cr() -> None:
                nonlocal cr_active
                if cr_active:
                    console.print()
                    cr_active = False

            if msg.startswith("Backing up night"):
                overwrite(ICON_BACKUP)
                cr_active = True
            elif msg.startswith("Backing up"):
                finish_cr()
                console.print(f"\n{ICON_BACKUP} {msg}")
            elif msg.startswith("Backed up to") or msg.startswith("Found "):
                finish_cr()
                print_success(msg)
            elif msg.startswith("Parsing session"):
                overwrite(ICON_SCAN)
                cr_active = True
            elif msg.startswith("Importing session"):
                overwrite(ICON_IMPORT)
                cr_active = True
            elif "orphaned" in msg or "skipping backup" in msg.lower():
                finish_cr()
                print_warning(msg)
            else:
                finish_cr()
                print_info(msg, indent=1)

        if dry_run:
            # Dry-run: parse sessions in CLI for detailed per-session display
            if parser is None:
                print_warning(f"Parser {source.parser_name!r} not found — skipping")
                continue

            parse_root = Path(source.root_path)
            try:
                sessions = list(
                    parser.parse_sessions(
                        parse_root,
                        date_from=date_from_str,
                        date_to=date_to_str,
                        limit=limit,
                        sort_by=sort_by if sort_by != "filesystem" else None,
                        parallel=not no_parallel,
                        progress_callback=_progress,
                    )
                )
            except Exception as e:
                if logging.getLogger().level == logging.DEBUG:
                    raise
                if len(selected_sources) > 1:
                    print_warning(f"Error parsing sessions for {source_desc}: {e}")
                    continue
                raise click.ClickException(f"Error parsing sessions: {e}") from e

            if not sessions:
                print_warning("No sessions found")
                if len(selected_sources) > 1:
                    continue
                return

            print_success(f"Found {len(sessions)} sessions")
            print_dry_run_header()
            console.print(
                f"{'Date':<12} {'Time':<8} {'Duration':<10} {'AHI':<6} {'Events':<8}"
            )
            print_footer()

            total_duration = 0.0
            total_events = 0

            for unified_session in sorted(
                sessions, key=lambda s: s.start_time, reverse=True
            ):
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

                console.print(
                    f"{unified_session.start_time:%Y-%m-%d}   {unified_session.start_time:%H:%M:%S}  "
                    f"{duration_hours:>6.1f}h    "
                    f"{ahi_str:>5}  "
                    f"{num_events:>6}"
                )

            print_footer()
            print_header("Summary", ICON_STATS)
            print_info(f"• Total sessions: {len(sessions)}", indent=1)
            print_info(f"• Total duration: {total_duration:.1f} hours", indent=1)
            print_info(f"• Total events: {total_events}", indent=1)
            if sessions:
                first_date = min(s.start_time for s in sessions)
                last_date = max(s.start_time for s in sessions)
                print_info(
                    f"• Date range: {first_date:%Y-%m-%d} to {last_date:%Y-%m-%d}",
                    indent=1,
                )
            if len(selected_sources) == 1:
                print_dry_run_complete("import")
            continue

        # Real import — delegate backup + parse + import to service
        try:
            result = asyncio.run(
                service.import_sources(
                    [source],
                    force=force,
                    batch_size=batch_size,
                    backup=not no_backup,
                    backup_root=None,
                    sort_by=sort_by if sort_by != "filesystem" else None,
                    limit=limit,
                    date_from=date_from_str,
                    date_to=date_to_str,
                    parallel=not no_parallel,
                    progress_callback=_progress,
                    user_ref=actor_user,
                    profile_ref=actor_profile,
                )
            )
        except RuntimeError as e:
            if logging.getLogger().level == logging.DEBUG:
                raise
            if len(selected_sources) > 1:
                print_warning(f"Import failed for {source_desc}: {e}")
                continue
            raise click.ClickException(str(e)) from e

        source_result = result.sources[0] if result.sources else None
        imported = source_result.imported if source_result else 0
        skipped = source_result.skipped if source_result else 0
        failed = source_result.failed if source_result else 0

        total_imported += imported
        total_skipped += skipped
        total_failed += failed

        if len(selected_sources) > 1:
            print_header(f"Summary for {source_desc}", ICON_STATS)
            print_success(f"Imported: {imported} sessions")
            if skipped > 0:
                print_skip(f"Skipped:  {skipped} sessions")
            if failed > 0:
                print_error(f"Failed:   {failed} sessions")

    if dry_run and len(selected_sources) > 1:
        print_header("Overall Dry Run Summary", ICON_STATS)
        print_success(f"Total data sources: {len(selected_sources)}")
        print_dry_run_complete("import")
        return
    elif dry_run:
        return

    print_header("Overall Import Summary", ICON_STATS)
    print_success(f"Imported: {total_imported} sessions")
    if total_skipped > 0:
        print_skip(
            f"Skipped:  {total_skipped} sessions (already exist, use --force to re-import)"
        )
    if total_failed > 0:
        print_error(f"Failed:   {total_failed} sessions")

    print_footer()

    if total_failed > 0:
        raise click.ClickException(f"{total_failed} session(s) failed to import")
