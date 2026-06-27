"""Database management commands."""

from __future__ import annotations

from pathlib import Path

import click

from snore.cli.decorators import db_option, db_session
from snore.cli.display import (
    ICON_STATS,
    console,
    print_footer,
    print_header,
    print_kv,
    print_subsection,
    print_success,
    print_warning,
)
from snore.constants import DEFAULT_DATABASE_PATH
from snore.database.models import Base
from snore.database.session import cleanup_database, init_database, session_scope
from snore.services.database_service import DatabaseService


@click.group()
def db() -> None:
    """Database management commands."""
    pass


@db.command()
@db_option
def init(db: str | None) -> None:
    """Initialize database (creates tables if needed)."""
    db_path = Path(db).expanduser() if db else Path(DEFAULT_DATABASE_PATH)
    db_existed = db_path.exists()

    init_database(str(db_path))

    table_names = sorted(Base.metadata.tables.keys())

    if db_existed:
        print_success(f"Database already initialized at {db_path}")
        console.print("\nVerified tables:")
    else:
        print_success(f"Created new database at {db_path}")
        console.print("\nInitialized tables:")

    for table_name in table_names:
        console.print(f"    - {table_name}")

    if db_existed:
        console.print("\nNo changes needed - all tables exist")


@db.command("stats")
@db_option
def db_stats(db: str | None) -> None:
    """Show database statistics."""
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    with db_session(db) as session:
        service = DatabaseService(session)
        stats = service.get_stats(str(db_path))

        print_header("Database Statistics", ICON_STATS)
        print_kv("Database", str(stats.db_path), indent=0)
        print_kv("Size", f"{stats.size_mb:.1f} MB", indent=0)

        print_subsection("Row Counts")
        print_kv("Profiles", str(stats.profile_count))
        print_kv("Devices", str(stats.device_count))
        print_kv("Sessions", str(stats.session_count))
        print_kv("Days", str(stats.day_count))
        print_kv("Events", str(stats.event_count))
        print_kv("Waveforms", str(stats.waveform_count))
        print_kv("Analysis Results", str(stats.analysis_count))
        print_kv("Detected Patterns", str(stats.pattern_count))

        print_subsection("Data Coverage")
        print_kv(
            "Sessions with waveforms",
            f"{stats.sessions_with_waveforms}/{stats.session_count} ({stats.waveform_coverage_pct:.1f}%)",
        )
        print_kv(
            "Sessions with events",
            f"{stats.sessions_with_events}/{stats.session_count} ({stats.event_coverage_pct:.1f}%)",
        )
        print_kv(
            "Sessions analyzed",
            f"{stats.analysis_count}/{stats.session_count} ({stats.analysis_coverage_pct:.1f}%)",
        )

        if stats.first_session and stats.last_session:
            console.print(
                f"\nDate range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
            )

        print_footer()


@db.command()
@db_option
@click.confirmation_option(prompt="Are you sure you want to vacuum the database?")
def vacuum(db: str | None) -> None:
    """Optimize database (reclaim space after deletions)."""
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    console.print("Vacuuming database...")

    with db_session(db) as session:
        service = DatabaseService(session)
        result = service.vacuum(str(db_path))

    print_success(
        f"Database vacuumed successfully ({result.size_before_mb:.1f} MB → {result.size_after_mb:.1f} MB)"
    )


@db.command()
@db_option
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def drop(db: str | None, force: bool) -> None:
    """Drop database (permanently delete all CPAP data)."""
    db_path = Path(db).expanduser() if db else Path(DEFAULT_DATABASE_PATH)

    if not db_path.exists():
        console.print(f"Database does not exist at {db_path}")
        return

    try:
        init_database(str(db_path))
        with session_scope() as session:
            service = DatabaseService(session)
            stats = service.get_stats(str(db_path))

            console.print(f"\nDatabase: {db_path}")
            console.print(f"Size: {stats.size_mb:.1f} MB")
            console.print(f"Devices: {stats.device_count}")
            console.print(f"Sessions: {stats.session_count}")
            console.print(f"Events: {stats.event_count:,}")

            if stats.first_session and stats.last_session:
                console.print(
                    f"Date range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
                )

    except Exception as e:
        print_warning(f"Could not read database stats: {e}")

    if not force:
        print_warning("WARNING: This will permanently delete all CPAP data!")
        if not click.confirm(
            "Are you sure you want to drop the database?", default=False
        ):
            console.print("Database drop cancelled")
            return

    try:
        cleanup_database()
    except Exception as e:
        print_warning(f"Warning during cleanup: {e}")

    try:
        if db_path.exists():
            db_path.unlink()
            print_success(f"Deleted database: {db_path}")

        for ext in ["-wal", "-shm"]:
            wal_file = Path(str(db_path) + ext)
            if wal_file.exists():
                wal_file.unlink()
                print_success(f"Deleted: {wal_file.name}")

        console.print("\nDatabase dropped successfully")

    except Exception as e:
        raise click.ClickException(f"Error dropping database: {e}") from e
