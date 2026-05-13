"""import command — import CPAP data from device SD card or directory."""

from __future__ import annotations

import logging

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import date_range_options, db_option, init_db
from snore.database.importers import SessionImporter
from snore.database.session import session_scope
from snore.parsers.register_all import register_all_parsers
from snore.parsers.registry import parser_registry


@click.command("import")
@click.argument("path", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Re-import existing sessions")
@db_option
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
@click.option(
    "--backup-dir",
    type=click.Path(file_okay=False),
    help="Raw backup directory (default: ~/.snore/raw/)",
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
    no_backup: bool,
    backup_dir: str | None,
) -> None:
    """Import CPAP data from device SD card or directory."""
    data_path = Path(path)

    register_all_parsers()

    click.echo(f"📂 Scanning {data_path}...")
    results = parser_registry.detect_all_parsers(data_path)

    if not results:
        click.echo("❌ Error: No compatible parser found for this data", err=True)
        click.echo("\nSupported devices:")
        for p in parser_registry.list_parsers():
            click.echo(f"  - {p.manufacturer}: {p.parser_id}")
        raise SystemExit(1)

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
                        raise SystemExit(1)
                except (ValueError, IndexError):
                    click.echo(f"❌ Invalid selection: {selection}", err=True)
                    raise SystemExit(1) from None
    else:
        selected_sources = expanded_sources

    init_db(db)

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

        root_path = source.get("root_path")
        parse_root = Path(str(root_path)) if root_path else data_path

        # Backup raw files before parsing (matching OSCAR's behavior)
        if not no_backup and not dry_run and parser.supports_raw_backup:
            device_serial = str(source.get("device_serial", ""))
            if device_serial:
                from snore.services.backup_service import BackupService

                backup_svc = BackupService(
                    Path(backup_dir).expanduser() if backup_dir else None
                )
                try:
                    click.echo("\n📦 Backing up raw files...")
                    backup_result = backup_svc.backup_via_parser(
                        parser,
                        parse_root,
                        device_serial,
                        progress_callback=lambda msg: click.echo(f"  {msg}"),
                    )
                    if backup_result.was_skipped:
                        click.echo(f"  Skipped: {backup_result.skipped_reason}")
                    else:
                        click.echo(f"✓ Backed up to {backup_result.backup_root}")
                    if not backup_result.was_skipped:
                        parse_root = backup_result.backup_root
                except Exception as e:
                    click.echo(
                        f"❌ Backup failed: {e}\n"
                        "  Import aborted. Use --no-backup to skip backup.",
                        err=True,
                    )
                    if logging.getLogger().level == logging.DEBUG:
                        raise
                    if len(selected_sources) > 1:
                        continue
                    raise SystemExit(1) from None
            else:
                click.echo(
                    "⚠️  No device serial found — skipping backup",
                    err=True,
                )

        click.echo("\n📋 Parsing sessions...")
        try:
            sessions = list(
                parser.parse_sessions(
                    parse_root,
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
            raise SystemExit(1) from None

        if not sessions:
            click.echo("⚠️  No sessions found")
            if len(selected_sources) > 1:
                continue
            return

        click.echo(f"✓ Found {len(sessions)} sessions")

        if dry_run:
            click.echo("\n🔍 DRY RUN MODE - No data will be imported\n")
            click.echo(
                f"{'Date':<12} {'Time':<8} {'Duration':<10} {'AHI':<6} {'Events':<8}"
            )
            click.echo("=" * 60)

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

            click.echo("=" * 60)
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
            click.echo(f"\n{'=' * 60}")
            click.echo(f"📊 Summary for {source_desc}")
            click.echo(f"{'=' * 60}")
            click.echo(f"✓ Imported: {imported} sessions")
            if skipped > 0:
                click.echo(f"⊝ Skipped:  {skipped} sessions")
            if failed > 0:
                click.echo(f"❌ Failed:   {failed} sessions")

    if dry_run and len(selected_sources) > 1:
        click.echo(f"\n{'=' * 60}")
        click.echo("📊 Overall Dry Run Summary")
        click.echo(f"{'=' * 60}")
        click.echo(f"✓ Total data sources: {len(selected_sources)}")
        click.echo("\n✓ Dry run complete. Use without --dry-run to import.")
        return
    elif dry_run:
        return

    click.echo(f"\n{'=' * 60}")
    click.echo("📊 Overall Import Summary")
    click.echo(f"{'=' * 60}")
    click.echo(f"✓ Imported: {total_imported} sessions")
    if total_skipped > 0:
        click.echo(
            f"⊝ Skipped:  {total_skipped} sessions (already exist, use --force to re-import)"
        )
    if total_failed > 0:
        click.echo(f"❌ Failed:   {total_failed} sessions")

    click.echo(f"{'=' * 60}")

    if total_failed > 0:
        raise SystemExit(1)
