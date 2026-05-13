"""Session management commands."""

from __future__ import annotations

from datetime import datetime

import click

from snore.cli.decorators import date_range_options, db_option, init_db, parse_id_list
from snore.constants import DEFAULT_LIST_SESSIONS_LIMIT


@click.group()
def session() -> None:
    """Session management commands."""
    pass


@session.command("list")
@click.option("--device", "-d", help="Filter by device serial number")
@date_range_options
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
@db_option
def session_list(
    device: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    sort_by: str,
    show_all: bool,
    db: str | None,
) -> None:
    """List imported sessions."""
    from snore.database.session import session_scope
    from snore.services.session_service import SessionService

    init_db(db)

    with session_scope() as db_session:
        service = SessionService(db_session)
        result = service.list_sessions(
            device=device,
            from_date=date_from,
            to_date=date_to,
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
        click.echo("-" * 80)

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
@db_option
def session_show(session_id: int, show_settings: bool, db: str | None) -> None:
    """Show details for a specific session."""
    from snore.database.session import session_scope
    from snore.services.session_service import SessionService
    from snore.utils.display import display_session_detail

    init_db(db)

    with session_scope() as db_session:
        service = SessionService(db_session)

        try:
            detail = service.get_session_detail(
                session_id, include_settings=show_settings
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e

        display_session_detail(detail, show_settings)


@session.command("delete")
@click.option("--device", "-d", help="Filter by device serial number")
@click.option(
    "--session-id",
    "session_ids",
    type=str,
    help="Comma-separated session IDs to delete (e.g., '1,2,3')",
)
@date_range_options
@click.option("--all", "delete_all", is_flag=True, help="Delete all sessions")
@click.option(
    "--dry-run", is_flag=True, help="Preview what would be deleted without deleting"
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@db_option
def session_delete(
    device: str | None,
    session_ids: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    delete_all: bool,
    dry_run: bool,
    force: bool,
    db: str | None,
) -> None:
    """Delete sessions from the database."""
    from snore.database.session import session_scope
    from snore.services.session_service import SessionService

    init_db(db)

    id_list = None
    if session_ids:
        id_list = parse_id_list(session_ids)

    with session_scope() as db_session:
        service = SessionService(db_session)

        try:
            preview = service.get_delete_preview(
                device=device,
                session_ids=id_list,
                from_date=date_from,
                to_date=date_to,
                delete_all=delete_all,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e

        if not preview.sessions:
            click.echo("⚠️  No sessions found matching the specified criteria")
            return

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
            return

        if not force:
            click.echo("⚠️  WARNING: This action cannot be undone!")
            if not click.confirm("Are you sure you want to delete these sessions?"):
                click.echo("Deletion cancelled")
                return

        session_ids_to_delete = [s.id for s in preview.sessions]
        deleted_count = service.delete_sessions(session_ids_to_delete)

        click.echo(
            f"\n✓ Successfully deleted {deleted_count} session(s) and related data"
        )

        if deleted_count > 10:
            click.echo("\n💡 Tip: Run 'snore db vacuum' to reclaim disk space")


def _toggle_session(session_id: int, enabled: bool, db: str | None) -> None:
    """Enable or disable a session and recalculate day statistics."""
    from snore.database.session import session_scope
    from snore.services.session_service import SessionService

    init_db(db)

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
            raise click.ClickException(str(e)) from e


@session.command("enable")
@click.argument("session_id", type=int)
@db_option
def session_enable(session_id: int, db: str | None) -> None:
    """Enable a session and recalculate day statistics."""
    _toggle_session(session_id, enabled=True, db=db)


@session.command("disable")
@click.argument("session_id", type=int)
@db_option
def session_disable(session_id: int, db: str | None) -> None:
    """Disable a session and recalculate day statistics."""
    _toggle_session(session_id, enabled=False, db=db)
