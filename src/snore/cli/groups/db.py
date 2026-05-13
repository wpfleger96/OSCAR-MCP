"""Database management commands."""

from __future__ import annotations

from pathlib import Path

import click

from snore.cli.decorators import db_option, init_db
from snore.constants import DEFAULT_DATABASE_PATH
from snore.database.models import Base
from snore.database.session import cleanup_database, init_database, session_scope
from snore.services.database_service import DatabaseService


@click.group()
def db() -> None:
    """Database management commands."""
    pass


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
def init(db: str | None) -> None:
    """Initialize database (creates tables if needed)."""
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)
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


@db.command("stats")
@db_option
def db_stats(db: str | None) -> None:
    """Show database statistics."""
    init_db(db)
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    with session_scope() as session:
        service = DatabaseService(session)
        stats = service.get_stats(str(db_path))

        click.echo("\n📊 Database Statistics")
        click.echo(f"{'=' * 60}")
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

        click.echo(f"{'=' * 60}\n")


@db.command()
@db_option
@click.confirmation_option(prompt="Are you sure you want to vacuum the database?")
def vacuum(db: str | None) -> None:
    """Optimize database (reclaim space after deletions)."""
    from sqlalchemy import text

    init_db(db)

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
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    if not db_path.exists():
        click.echo(f"Database does not exist at {db_path}")
        return

    try:
        init_database(str(db_path))
        with session_scope() as session:
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
        raise click.ClickException(f"Error dropping database: {e}") from e
