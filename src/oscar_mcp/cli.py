"""
Command-line interface for OSCAR-MCP.

Provides commands for importing CPAP data, querying sessions, and database management.
"""

import click
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

from oscar_mcp.database import DatabaseManager, SessionImporter
from oscar_mcp.database.session import session_scope, init_database
from oscar_mcp.database import models
from oscar_mcp.analysis.service import AnalysisService
from oscar_mcp.parsers.registry import parser_registry
from oscar_mcp.parsers.register_all import register_all_parsers

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose):
    """OSCAR-MCP: CPAP Data Management Tool"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Re-import existing sessions")
@click.option("--db", type=click.Path(), help="Database path (default: ~/.oscar-mcp/oscar_mcp.db)")
@click.option("--limit", "-n", type=int, help="Limit to first N sessions")
@click.option(
    "--sort-by",
    type=click.Choice(["date-asc", "date-desc", "filesystem"]),
    default="filesystem",
    help="Session sort order (default: filesystem)",
)
@click.option(
    "--date-from",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Import sessions from this date (YYYY-MM-DD)",
)
@click.option(
    "--date-to",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Import sessions up to this date (YYYY-MM-DD)",
)
@click.option("--dry-run", is_flag=True, help="Show what would be imported without importing")
def import_data(
    path: str,
    force: bool,
    db: Optional[str],
    limit: Optional[int],
    sort_by: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    dry_run: bool,
):
    """Import CPAP data from device SD card or directory."""
    data_path = Path(path)

    # Register parsers
    register_all_parsers()

    # Auto-detect parser
    click.echo(f"📂 Scanning {data_path}...")
    parser = parser_registry.detect_parser(data_path)

    if not parser:
        click.echo("❌ Error: No compatible parser found for this data", err=True)
        click.echo("\nSupported devices:")
        for p in parser_registry.list_parsers():
            click.echo(f"  - {p.manufacturer}: {p.parser_id}")
        return 1

    click.echo(f"✓ Detected: {parser.manufacturer} ({parser.parser_id})")

    # Format date parameters for parser
    date_from_str = date_from.strftime("%Y-%m-%d") if date_from else None
    date_to_str = date_to.strftime("%Y-%m-%d") if date_to else None

    # Show filter summary if any filters are active
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

    # Parse sessions
    click.echo("\n📋 Parsing sessions...")
    try:
        sessions = list(
            parser.parse_sessions(
                data_path,
                date_from=date_from_str,
                date_to=date_to_str,
                limit=limit,
                sort_by=sort_by if sort_by != "filesystem" else None,
            )
        )
    except Exception as e:
        click.echo(f"❌ Error parsing sessions: {e}", err=True)
        if logging.getLogger().level == logging.DEBUG:
            raise
        return 1

    if not sessions:
        click.echo("⚠️  No sessions found")
        return 0

    click.echo(f"✓ Found {len(sessions)} sessions")

    # Dry-run mode: show what would be imported
    if dry_run:
        click.echo("\n🔍 DRY RUN MODE - No data will be imported\n")
        click.echo(f"{'Date':<12} {'Time':<8} {'Duration':<10} {'AHI':<6} {'Events':<8}")
        click.echo("=" * 55)

        total_duration = 0.0
        total_events = 0

        # Sort sessions by date descending for display
        sorted_sessions = sorted(sessions, key=lambda s: s.start_time, reverse=True)

        for session in sorted_sessions:
            duration_hours = session.duration_seconds / 3600 if session.duration_seconds else 0
            total_duration += duration_hours

            # Count events
            num_events = len(session.events) if session.events else 0
            total_events += num_events

            # Get AHI from statistics if available
            ahi_str = "N/A"
            if hasattr(session, "statistics") and session.statistics:
                if session.statistics.ahi is not None:
                    ahi_str = f"{session.statistics.ahi:.1f}"

            click.echo(
                f"{session.start_time:%Y-%m-%d}   {session.start_time:%H:%M:%S}  "
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
            # Calculate actual date range using min/max
            first_date = min(s.start_time for s in sessions)
            last_date = max(s.start_time for s in sessions)
            click.echo(f"  • Date range: {first_date:%Y-%m-%d} to {last_date:%Y-%m-%d}")
        click.echo("\n✓ Dry run complete. Use without --dry-run to import.")
        return 0

    # Initialize database
    db_manager = DatabaseManager(db_path=Path(db) if db else None)
    importer = SessionImporter(db_manager)

    # Import sessions
    imported = 0
    skipped = 0
    failed = 0

    with click.progressbar(
        sessions,
        label="Importing sessions",
        show_pos=True,
        item_show_func=lambda s: f"{s.start_time:%Y-%m-%d}" if s else "",
    ) as bar:
        for session in bar:
            try:
                if importer.import_session(session, force=force):
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                logger.error(f"Failed to import session {session.device_session_id}: {e}")

    # Summary
    click.echo(f"\n{'=' * 50}")
    click.echo("📊 Import Summary")
    click.echo(f"{'=' * 50}")
    click.echo(f"✓ Imported: {imported} sessions")
    if skipped > 0:
        click.echo(f"⊝ Skipped:  {skipped} sessions (already exist, use --force to re-import)")
    if failed > 0:
        click.echo(f"❌ Failed:   {failed} sessions")

    # Show date range of imported sessions
    if sessions and imported > 0:
        imported_sessions = [s for i, s in enumerate(sessions) if i < imported + skipped]
        if imported_sessions:
            first_date = min(s.start_time for s in imported_sessions)
            last_date = max(s.start_time for s in imported_sessions)
            click.echo(f"\n📅 Date range: {first_date:%Y-%m-%d} to {last_date:%Y-%m-%d}")

            # Calculate total duration
            total_hours = sum(
                (s.duration_seconds / 3600 if s.duration_seconds else 0) for s in imported_sessions
            )
            click.echo(f"⏱️  Total duration: {total_hours:.1f} hours")

    click.echo(f"{'=' * 50}")

    if failed > 0:
        return 1
    return 0


@cli.command("list-sessions")
@click.option(
    "--from-date",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to-date", "to_date", type=click.DateTime(formats=["%Y-%m-%d"]), help="End date (YYYY-MM-DD)"
)
@click.option("--limit", type=int, default=20, help="Max sessions to show")
@click.option("--db", type=click.Path(), help="Database path")
def list_sessions(
    from_date: Optional[datetime], to_date: Optional[datetime], limit: int, db: Optional[str]
):
    """List imported sessions."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)

    # Build query
    query = "SELECT * FROM sessions JOIN devices ON sessions.device_id = devices.id"
    params: list[Any] = []
    conditions = []

    if from_date:
        conditions.append("start_time >= ?")
        params.append(from_date)

    if to_date:
        conditions.append("start_time <= ?")
        params.append(to_date)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    # Execute query
    with db_manager.get_connection() as conn:
        cursor = conn.execute(query, params)
        sessions = cursor.fetchall()

    if not sessions:
        click.echo("No sessions found")
        return

    # Display sessions
    click.echo(f"\n{'Date':<12} {'Time':<8} {'Duration':<10} {'Device':<20} {'AHI':<6}")
    click.echo("=" * 70)

    for session in sessions:
        start = session["start_time"]
        duration_hours = session["duration_seconds"] / 3600 if session["duration_seconds"] else 0
        device_name = f"{session['manufacturer']} {session['model']}"

        # Get AHI from statistics if available
        stats_query = "SELECT ahi FROM statistics WHERE session_id = ?"
        cursor = db_manager.get_connection().execute(stats_query, (session["id"],))
        stats = cursor.fetchone()
        ahi = f"{stats['ahi']:.1f}" if stats and stats["ahi"] is not None else "N/A"

        click.echo(
            f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
            f"{duration_hours:>6.1f}h    "
            f"{device_name:<20} "
            f"{ahi:>5}"
        )


@cli.command("delete-sessions")
@click.option(
    "--session-id",
    "session_ids",
    type=str,
    help="Comma-separated session IDs to delete (e.g., '1,2,3')",
)
@click.option(
    "--from-date",
    "from_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete sessions from this date (YYYY-MM-DD)",
)
@click.option(
    "--to-date",
    "to_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Delete sessions up to this date (YYYY-MM-DD)",
)
@click.option("--all", "delete_all", is_flag=True, help="Delete all sessions")
@click.option("--dry-run", is_flag=True, help="Preview what would be deleted without deleting")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.option("--db", type=click.Path(), help="Database path")
def delete_sessions(
    session_ids: Optional[str],
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    delete_all: bool,
    dry_run: bool,
    force: bool,
    db: Optional[str],
):
    """Delete sessions from the database."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)

    # Validate that at least one filter is provided
    if not any([session_ids, from_date, to_date, delete_all]):
        click.echo("❌ Error: You must specify at least one filter:", err=True)
        click.echo("  • --session-id <ids>")
        click.echo("  • --from-date <date>")
        click.echo("  • --to-date <date>")
        click.echo("  • --all")
        return 1

    # Build query to select sessions
    query = """
        SELECT
            sessions.id,
            sessions.device_session_id,
            sessions.start_time,
            sessions.duration_seconds,
            devices.manufacturer,
            devices.model,
            devices.serial_number
        FROM sessions
        JOIN devices ON sessions.device_id = devices.id
    """
    params: list[Any] = []
    conditions = []

    # Apply filters
    if session_ids:
        # Parse comma-separated IDs
        try:
            id_list = [int(sid.strip()) for sid in session_ids.split(",")]
            placeholders = ",".join("?" * len(id_list))
            conditions.append(f"sessions.id IN ({placeholders})")
            params.extend(id_list)
        except ValueError:
            click.echo(
                "❌ Error: Invalid session ID format. Use comma-separated integers (e.g., '1,2,3')",
                err=True,
            )
            return 1

    if from_date:
        conditions.append("sessions.start_time >= ?")
        params.append(from_date)

    if to_date:
        conditions.append("sessions.start_time <= ?")
        params.append(to_date)

    # Add WHERE clause if conditions exist
    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY sessions.start_time DESC"

    # Execute query to get sessions
    with db_manager.get_connection() as conn:
        cursor = conn.execute(query, params)
        sessions = cursor.fetchall()

    if not sessions:
        click.echo("⚠️  No sessions found matching the specified criteria")
        return 0

    # Count related data
    session_ids_to_delete = [s["id"] for s in sessions]
    placeholders = ",".join("?" * len(session_ids_to_delete))

    with db_manager.get_connection() as conn:
        # Count events
        cursor = conn.execute(
            f"SELECT COUNT(*) as count FROM events WHERE session_id IN ({placeholders})",
            session_ids_to_delete,
        )
        event_count = cursor.fetchone()["count"]

        # Count waveforms
        cursor = conn.execute(
            f"SELECT COUNT(*) as count FROM waveforms WHERE session_id IN ({placeholders})",
            session_ids_to_delete,
        )
        waveform_count = cursor.fetchone()["count"]

        # Count statistics
        cursor = conn.execute(
            f"SELECT COUNT(*) as count FROM statistics WHERE session_id IN ({placeholders})",
            session_ids_to_delete,
        )
        stats_count = cursor.fetchone()["count"]

    # Display sessions to be deleted
    click.echo(f"\n{'=' * 70}")
    if dry_run:
        click.echo("🔍 DRY RUN MODE - No data will be deleted")
    else:
        click.echo("⚠️  Sessions to be DELETED")
    click.echo(f"{'=' * 70}\n")

    click.echo(f"{'ID':<5} {'Date':<12} {'Time':<8} {'Duration':<10} {'Device':<25}")
    click.echo("-" * 70)

    for session in sessions:
        start = session["start_time"]
        duration_hours = session["duration_seconds"] / 3600 if session["duration_seconds"] else 0
        device_name = f"{session['manufacturer']} {session['model']}"

        click.echo(
            f"{session['id']:<5} "
            f"{start:%Y-%m-%d}   {start:%H:%M:%S}  "
            f"{duration_hours:>6.1f}h    "
            f"{device_name:<25}"
        )

    # Display summary
    click.echo("\n" + "=" * 70)
    click.echo("📊 Deletion Summary")
    click.echo("=" * 70)
    click.echo(f"Sessions:    {len(sessions)}")
    click.echo(f"Events:      {event_count}")
    click.echo(f"Waveforms:   {waveform_count}")
    click.echo(f"Statistics:  {stats_count}")
    click.echo("=" * 70 + "\n")

    # Dry-run mode: exit without deleting
    if dry_run:
        click.echo("✓ Dry run complete. Use without --dry-run to delete.")
        return 0

    # Confirmation prompt (unless --force)
    if not force:
        click.echo("⚠️  WARNING: This action cannot be undone!")
        if not click.confirm("Are you sure you want to delete these sessions?"):
            click.echo("Deletion cancelled")
            return 0

    # Perform deletion with transaction
    try:
        with db_manager.transaction() as conn:
            # Delete related data first (though CASCADE should handle this)
            conn.execute(
                f"DELETE FROM events WHERE session_id IN ({placeholders})",
                session_ids_to_delete,
            )
            conn.execute(
                f"DELETE FROM waveforms WHERE session_id IN ({placeholders})",
                session_ids_to_delete,
            )
            conn.execute(
                f"DELETE FROM statistics WHERE session_id IN ({placeholders})",
                session_ids_to_delete,
            )
            conn.execute(
                f"DELETE FROM settings WHERE session_id IN ({placeholders})",
                session_ids_to_delete,
            )
            # Finally delete the sessions
            conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})",
                session_ids_to_delete,
            )

        click.echo(f"\n✓ Successfully deleted {len(sessions)} session(s) and related data")

        # Suggest vacuum for large deletions
        if len(sessions) > 10:
            click.echo("\n💡 Tip: Run 'oscar-mcp db vacuum' to reclaim disk space")

    except Exception as e:
        click.echo(f"\n❌ Error during deletion: {e}", err=True)
        logger.exception("Deletion failed")
        return 1

    return 0


@cli.group()
def db():
    """Database management commands."""
    pass


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
def init(db: Optional[str]):
    """Initialize database (creates tables if needed)."""
    db_path = Path(db) if db else None
    db_manager = DatabaseManager(db_path=db_path)
    click.echo(f"✓ Database initialized at {db_manager.db_path}")


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
def stats(db: Optional[str]):
    """Show database statistics."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)

    stats = db_manager.get_stats()

    click.echo("\n📊 Database Statistics")
    click.echo(f"{'=' * 50}")
    click.echo(f"Database: {db_manager.db_path}")
    click.echo(f"Size: {stats['size_mb']:.1f} MB")
    click.echo(f"\nDevices: {stats['devices']}")
    click.echo(f"Sessions: {stats['sessions']}")
    click.echo(f"Events: {stats['events']}")

    if stats["first_session"] and stats["last_session"]:
        first = stats["first_session"]
        last = stats["last_session"]
        click.echo(f"\nDate range: {first:%Y-%m-%d} to {last:%Y-%m-%d}")

    click.echo(f"{'=' * 50}\n")


@db.command()
@click.option("--db", type=click.Path(), help="Database path")
@click.confirmation_option(prompt="Are you sure you want to vacuum the database?")
def vacuum(db: Optional[str]):
    """Optimize database (reclaim space after deletions)."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)

    click.echo("Vacuuming database...")
    with db_manager.get_connection() as conn:
        conn.execute("VACUUM")

    click.echo("✓ Database vacuumed successfully")


@cli.group()
def analyze():
    """Run programmatic analysis on CPAP sessions."""
    pass


@analyze.command("session")
@click.option("--profile", required=True, help="Profile username")
@click.option("--date", type=click.DateTime(formats=["%Y-%m-%d"]), help="Session date (YYYY-MM-DD)")
@click.option("--session-id", type=int, help="Session ID (alternative to --date)")
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--no-store", is_flag=True, help="Don't store results in database")
def analyze_session(
    profile: str,
    date: Optional[datetime],
    session_id: Optional[int],
    db: Optional[str],
    no_store: bool,
):
    """Analyze a single CPAP session for events and patterns."""
    if not date and not session_id:
        click.echo("Error: Must provide either --date or --session-id", err=True)
        return 1

    db_manager = DatabaseManager(db_path=Path(db) if db else None)
    init_database(str(db_manager.db_path))

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=profile).first()
        if not prof:
            click.echo(f"Error: Profile '{profile}' not found", err=True)
            return 1

        if date:
            db_session = (
                session.query(models.Session)
                .join(models.Day)
                .filter(models.Day.profile_id == prof.id, models.Day.date == date.date())
                .first()
            )
            if not db_session:
                click.echo(f"Error: No session found for {date.date()}", err=True)
                return 1
            session_id = db_session.id
            session_date_str = date.date().isoformat()
        else:
            db_session = session.query(models.Session).filter_by(id=session_id).first()
            if not db_session:
                click.echo(f"Error: Session {session_id} not found", err=True)
                return 1
            session_date_str = db_session.start_time.date().isoformat()

        click.echo(f"\n📊 Analyzing session {session_date_str} (ID: {session_id})...")

        analysis_service = AnalysisService(session)

        try:
            result = analysis_service.analyze_session(
                session_id=session_id, store_results=not no_store
            )

            click.echo(f"✓ Analysis complete in {result.processing_time_ms}ms\n")

            click.echo("=" * 60)
            click.echo("ANALYSIS SUMMARY")
            click.echo("=" * 60)

            flow_analysis = result.flow_analysis
            event_timeline = result.event_timeline

            click.echo(f"\nSession Duration: {result.duration_hours:.1f} hours")
            click.echo(f"Total Breaths: {result.total_breaths:,}")

            click.echo(f"\n📈 RESPIRATORY INDICES")
            click.echo(f"  AHI (Apnea-Hypopnea Index): {event_timeline['ahi']:.1f} events/hour")
            click.echo(
                f"  RDI (Respiratory Disturbance Index): {event_timeline['rdi']:.1f} events/hour"
            )
            click.echo(f"  Flow Limitation Index: {flow_analysis['fl_index']:.2f}")

            click.echo(f"\n🫁 RESPIRATORY EVENTS (Total: {event_timeline['total_events']})")
            click.echo(f"  Apneas: {len(event_timeline['apneas'])}")
            for apnea_type in ["OA", "CA", "MA", "UA"]:
                count = sum(1 for a in event_timeline["apneas"] if a["event_type"] == apnea_type)
                if count > 0:
                    click.echo(f"    - {apnea_type}: {count}")
            click.echo(f"  Hypopneas: {len(event_timeline['hypopneas'])}")
            click.echo(f"  RERAs: {len(event_timeline['reras'])}")

            click.echo(f"\n💨 FLOW LIMITATION CLASSES")
            for fl_class, count in sorted(flow_analysis["class_distribution"].items()):
                if count > 0:
                    pct = (count / result.total_breaths) * 100
                    click.echo(f"  Class {fl_class}: {count:,} breaths ({pct:.1f}%)")

            if result.csr_detection:
                csr = result.csr_detection
                click.echo(f"\n🌊 CHEYNE-STOKES RESPIRATION")
                click.echo(f"  Detected: Yes (confidence: {csr['confidence']:.2f})")
                click.echo(f"  Cycle Length: {csr['cycle_length']:.0f}s")
                click.echo(f"  CSR Index: {csr['csr_index']:.1%}")

            if result.periodic_breathing:
                periodic = result.periodic_breathing
                click.echo(f"\n🔄 PERIODIC BREATHING")
                click.echo(f"  Detected: Yes (confidence: {periodic['confidence']:.2f})")
                click.echo(f"  Cycle Length: {periodic['cycle_length']:.0f}s")
                click.echo(f"  Regularity: {periodic['regularity_score']:.2f}")

            if result.positional_analysis:
                positional = result.positional_analysis
                click.echo(f"\n🛏️  POSITIONAL ANALYSIS")
                click.echo(f"  Event Clustering: {positional['cluster_count']} clusters")
                click.echo(f"  Positional Likelihood: {positional['positional_likelihood']:.2f}")

            if not no_store:
                stored = analysis_service.get_analysis_result(session_id)
                if stored:
                    click.echo(f"\n💾 Results stored with analysis ID: {stored['analysis_id']}")

            click.echo("\n" + "=" * 60)

        except Exception as e:
            click.echo(f"\n❌ Analysis failed: {e}", err=True)
            logger.error("Analysis error", exc_info=True)
            return 1


@analyze.command("sessions")
@click.option("--profile", required=True, help="Profile username")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), help="Start date (YYYY-MM-DD)")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), help="End date (YYYY-MM-DD)")
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--no-store", is_flag=True, help="Don't store results in database")
def analyze_sessions(
    profile: str,
    start: Optional[datetime],
    end: Optional[datetime],
    db: Optional[str],
    no_store: bool,
):
    """Analyze multiple CPAP sessions in a date range."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)
    init_database(str(db_manager.db_path))

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=profile).first()
        if not prof:
            click.echo(f"Error: Profile '{profile}' not found", err=True)
            return 1

        query = (
            session.query(models.Session).join(models.Day).filter(models.Day.profile_id == prof.id)
        )

        if start:
            query = query.filter(models.Day.date >= start.date())
        if end:
            query = query.filter(models.Day.date <= end.date())

        sessions = query.order_by(models.Day.date).all()

        if not sessions:
            click.echo("No sessions found for the specified criteria")
            return 0

        click.echo(f"\n📊 Analyzing {len(sessions)} sessions...")

        analysis_service = AnalysisService(session)
        successful = 0
        failed = 0

        with click.progressbar(sessions, label="Analyzing") as bar:
            for db_session in bar:
                try:
                    analysis_service.analyze_session(
                        session_id=db_session.id, store_results=not no_store
                    )
                    successful += 1
                except Exception as e:
                    failed += 1
                    logger.debug(f"Failed to analyze session {db_session.id}: {e}")

        click.echo(f"\n✓ Analysis complete")
        click.echo(f"  Successful: {successful}")
        click.echo(f"  Failed: {failed}")


@analyze.command("list")
@click.option("--profile", required=True, help="Profile username")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), help="Start date (YYYY-MM-DD)")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), help="End date (YYYY-MM-DD)")
@click.option("--db", type=click.Path(), help="Database path")
@click.option("--analyzed-only", is_flag=True, help="Show only analyzed sessions")
def list_sessions(
    profile: str,
    start: Optional[datetime],
    end: Optional[datetime],
    db: Optional[str],
    analyzed_only: bool,
):
    """List sessions and their analysis status."""
    db_manager = DatabaseManager(db_path=Path(db) if db else None)
    init_database(str(db_manager.db_path))

    with session_scope() as session:
        prof = session.query(models.Profile).filter_by(username=profile).first()
        if not prof:
            click.echo(f"Error: Profile '{profile}' not found", err=True)
            return 1

        query = (
            session.query(models.Session).join(models.Day).filter(models.Day.profile_id == prof.id)
        )

        if start:
            query = query.filter(models.Day.date >= start.date())
        if end:
            query = query.filter(models.Day.date <= end.date())

        sessions = query.order_by(models.Day.date.desc()).all()

        if not sessions:
            click.echo("No sessions found")
            return 0

        click.echo(f"\nSession Analysis Status ({len(sessions)} sessions)\n")
        click.echo(f"{'Date':<12} {'ID':<6} {'Duration':<10} {'Analyzed':<10} {'Analysis ID':<12}")
        click.echo("-" * 60)

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

            duration = (
                f"{db_session.duration_seconds / 3600:.1f}h"
                if db_session.duration_seconds
                else "N/A"
            )
            analyzed_str = "✓" if has_analysis else "✗"
            analysis_id_str = str(analysis.id) if analysis else "-"

            click.echo(
                f"{db_session.start_time.date()!s:<12} {db_session.id:<6} {duration:<10} "
                f"{analyzed_str:<10} {analysis_id_str:<12}"
            )


def main():
    """Main CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
