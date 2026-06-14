"""
Tests for CLI commands.

These tests verify the command-line interface functionality including:
- session delete command with various input modes
- profile list command output
- db stats command
- session list command with limits and truncation
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from click.testing import CliRunner
from sqlalchemy import text

from snore.cli import cli
from snore.database import models
from snore.database.day_manager import DayManager
from snore.database.session import init_database, session_scope


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def populated_test_db(temp_db):
    """Create a database populated with realistic test data."""
    init_database(str(temp_db))

    with session_scope() as session:
        device = models.Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST12345",
        )
        session.add(device)
        session.flush()

        base_time = datetime(2025, 10, 1, 22, 0, 0)
        for i in range(10):
            start_time = base_time + timedelta(days=i)
            end_time = start_time + timedelta(hours=8)

            sess = models.Session(
                device_id=device.id,
                device_session_id=f"test_session_{i}",
                start_time=start_time,
                end_time=end_time,
                duration_seconds=8 * 3600,
                has_statistics=True,
                has_event_data=True,
            )
            session.add(sess)
            session.flush()

            day_date = DayManager.get_day_for_session(start_time)
            day = DayManager.create_or_update_day(device.id, day_date, session)
            sess.day_id = day.id

            session.add(models.Setting(session_id=sess.id, key="mode", value="CPAP"))

            session.add(
                models.Event(
                    session_id=sess.id,
                    event_type="Apnea",
                    start_time=start_time + timedelta(hours=2),
                    duration_seconds=15.0,
                )
            )

            session.add(models.Statistics(session_id=sess.id, ahi=5.2, usage_hours=7.8))

        session.commit()

    return temp_db


@pytest.fixture
def populated_test_db_full(temp_db):
    """Create a database populated with full Statistics and Waveform records."""
    import numpy as np

    init_database(str(temp_db))

    with session_scope() as session:
        device = models.Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST12345",
        )
        session.add(device)
        session.flush()

        base_time = datetime(2025, 10, 1, 22, 0, 0)
        for i in range(10):
            start_time = base_time + timedelta(days=i)
            end_time = start_time + timedelta(hours=8)

            sess = models.Session(
                device_id=device.id,
                device_session_id=f"test_session_{i}",
                start_time=start_time,
                end_time=end_time,
                duration_seconds=8 * 3600,
                has_statistics=True,
                has_event_data=True,
                has_waveform_data=True,
            )
            session.add(sess)
            session.flush()

            day_date = DayManager.get_day_for_session(start_time)
            day = DayManager.create_or_update_day(device.id, day_date, session)
            sess.day_id = day.id

            session.add(models.Setting(session_id=sess.id, key="mode", value="CPAP"))

            session.add(
                models.Event(
                    session_id=sess.id,
                    event_type="Apnea",
                    start_time=start_time + timedelta(hours=2),
                    duration_seconds=15.0,
                )
            )

            session.add(
                models.Statistics(
                    session_id=sess.id,
                    ahi=5.2 + i * 0.3,
                    usage_hours=7.8,
                    rei=4.5,
                    obstructive_apneas=3,
                    central_apneas=1,
                    hypopneas=5,
                    pressure_mean=10.5,
                    pressure_min=8.0,
                    pressure_max=13.0,
                    pressure_95th=12.5,
                    leak_mean=12.0,
                    leak_percentile_70=15.0,
                    leak_95th=22.0,
                    spo2_mean=95.5,
                    spo2_min=88.0,
                    spo2_time_below_90=120,
                    pulse_mean=72.0,
                    pulse_min=55.0,
                    pulse_max=95.0,
                    respiratory_rate_mean=15.0,
                    tidal_volume_mean=450.0,
                    minute_ventilation_mean=6.8,
                )
            )

            dummy_blob = np.array([[0.0, 1.0], [1.0, 2.0]], dtype=np.float32).tobytes()
            for wtype in ["flow", "pressure", "leak"]:
                session.add(
                    models.Waveform(
                        session_id=sess.id,
                        waveform_type=wtype,
                        sample_rate=25.0 if wtype == "flow" else 0.5,
                        unit={"flow": "L/min", "pressure": "cmH2O", "leak": "L/min"}[
                            wtype
                        ],
                        data_blob=dummy_blob,
                        sample_count=2,
                    )
                )

        session.commit()

    return temp_db


class TestSessionDeleteCommand:
    """Test session delete command with various scenarios."""

    def test_delete_single_session_by_id(self, cli_runner, populated_test_db):
        """Test deleting a single session by ID."""
        result = cli_runner.invoke(
            cli,
            ["session", "delete", "--db", str(populated_test_db), "--session-id", "1"],
            input="y\n",
        )

        assert result.exit_code == 0
        assert "Successfully deleted 1 session(s)" in result.output

        with session_scope() as session:
            remaining = session.query(models.Session).filter_by(id=1).count()
            assert remaining == 0

    def test_delete_multiple_sessions_by_id(self, cli_runner, populated_test_db):
        """Test deleting multiple sessions by ID (tests SQL IN clause fix)."""
        result = cli_runner.invoke(
            cli,
            [
                "session",
                "delete",
                "--db",
                str(populated_test_db),
                "--session-id",
                "1,2,3",
            ],
            input="y\n",
        )

        assert result.exit_code == 0
        assert "Successfully deleted 3 session(s)" in result.output

        with session_scope() as session:
            remaining = (
                session.query(models.Session)
                .filter(models.Session.id.in_([1, 2, 3]))
                .count()
            )
            assert remaining == 0

    def test_delete_sessions_by_date_range(self, cli_runner, populated_test_db):
        """Test deleting sessions by date range."""
        result = cli_runner.invoke(
            cli,
            [
                "session",
                "delete",
                "--db",
                str(populated_test_db),
                "--from",
                "2025-10-01",
                "--to",
                "2025-10-03",
            ],
            input="y\n",
        )

        assert result.exit_code == 0
        assert "sessions" in result.output.lower()

        with session_scope() as session:
            total_remaining = session.query(models.Session).count()
            assert total_remaining < 10

    def test_delete_cascades_to_child_tables(self, cli_runner, populated_test_db):
        """Test that deleting sessions cascades to events, waveforms, statistics."""
        with session_scope() as session:
            events_before = session.execute(
                text("SELECT COUNT(*) FROM events WHERE session_id = 1")
            ).scalar()
            stats_before = session.execute(
                text("SELECT COUNT(*) FROM statistics WHERE session_id = 1")
            ).scalar()

        assert events_before > 0
        assert stats_before > 0

        result = cli_runner.invoke(
            cli,
            ["session", "delete", "--db", str(populated_test_db), "--session-id", "1"],
            input="y\n",
        )

        assert result.exit_code == 0

        with session_scope() as session:
            events_after = session.execute(
                text("SELECT COUNT(*) FROM events WHERE session_id = 1")
            ).scalar()
            stats_after = session.execute(
                text("SELECT COUNT(*) FROM statistics WHERE session_id = 1")
            ).scalar()

        assert events_after == 0
        assert stats_after == 0

    def test_delete_session_datetime_formatting(self, cli_runner, populated_test_db):
        """Test that datetime formatting works correctly in delete preview."""
        result = cli_runner.invoke(
            cli,
            ["session", "delete", "--db", str(populated_test_db), "--session-id", "1"],
            input="n\n",
        )

        assert result.exit_code == 0
        assert "2025-10-" in result.output
        assert "Deletion cancelled" in result.output


class TestDbStatsCommand:
    """Test db stats command."""

    def test_db_stats_datetime_formatting(self, cli_runner, populated_test_db):
        """Test that db stats correctly formats datetime values (tests string->datetime fix)."""
        result = cli_runner.invoke(cli, ["db", "stats", "--db", str(populated_test_db)])

        assert result.exit_code == 0
        assert "Database Statistics" in result.output
        assert "Devices: 1" in result.output
        assert "Sessions: 10" in result.output
        assert "Events: 10" in result.output
        assert "Date range:" in result.output
        assert "2025-10-" in result.output

    def test_db_stats_empty_database(self, cli_runner, temp_db):
        """Test db stats with empty database."""
        init_database(str(temp_db))

        result = cli_runner.invoke(cli, ["db", "stats", "--db", str(temp_db)])

        assert result.exit_code == 0
        assert "Devices: 0" in result.output
        assert "Sessions: 0" in result.output


class TestSessionListCommand:
    """Test session list command."""

    def test_list_sessions_default_limit(self, cli_runner, populated_test_db):
        """Test session list uses default limit of 20."""
        result = cli_runner.invoke(
            cli, ["session", "list", "--db", str(populated_test_db)]
        )

        assert result.exit_code == 0

        session_rows = [
            line
            for line in result.output.split("\n")
            if "2025-10-" in line and "TEST12345" in line
        ]
        assert len(session_rows) == 10

    def test_list_sessions_custom_limit(self, cli_runner, populated_test_db):
        """Test session list with custom limit."""
        result = cli_runner.invoke(
            cli, ["session", "list", "--db", str(populated_test_db), "--limit", "5"]
        )

        assert result.exit_code == 0

        session_rows = [
            line
            for line in result.output.split("\n")
            if "2025-10-" in line and "TEST12345" in line
        ]
        assert len(session_rows) == 5
        assert "Showing 5 of 10 sessions" in result.output
        assert "Tip:" in result.output

    def test_list_sessions_unlimited(self, cli_runner, populated_test_db):
        """Test session list with --limit 0 shows all sessions."""
        result = cli_runner.invoke(
            cli, ["session", "list", "--db", str(populated_test_db), "--limit", "0"]
        )

        assert result.exit_code == 0
        session_rows = [
            line
            for line in result.output.split("\n")
            if "2025-10-" in line and "TEST12345" in line
        ]
        assert len(session_rows) == 10

    def test_list_sessions_no_truncation_message(self, cli_runner, temp_db):
        """Test session list doesn't show truncation when all results fit."""
        init_database(str(temp_db))

        with session_scope() as session:
            device = models.Device(
                manufacturer="Test",
                model="Test",
                serial_number="TEST",
            )
            session.add(device)
            session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            sess = models.Session(
                device_id=device.id,
                device_session_id="test_session_1",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(sess)
            session.commit()

        result = cli_runner.invoke(cli, ["session", "list", "--db", str(temp_db)])

        assert result.exit_code == 0
        assert "Showing" not in result.output or "Showing all" in result.output


@pytest.fixture
def db_with_analysis(temp_db):
    """Create a database populated with sessions and analysis results."""
    init_database(str(temp_db))

    with session_scope() as session:
        device = models.Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST12345",
        )
        session.add(device)
        session.flush()

        base_time = datetime(2025, 10, 1, 22, 0, 0)
        for i in range(5):
            start_time = base_time + timedelta(days=i)
            end_time = start_time + timedelta(hours=8)

            sess = models.Session(
                device_id=device.id,
                device_session_id=f"test_session_{i}",
                start_time=start_time,
                end_time=end_time,
                duration_seconds=8 * 3600,
            )
            session.add(sess)
            session.flush()

            day_date = DayManager.get_day_for_session(start_time)
            day = DayManager.create_or_update_day(device.id, day_date, session)
            sess.day_id = day.id

            num_analyses = 3 if i < 2 else 1
            for j in range(num_analyses):
                analysis_json = {
                    "session_id": sess.id,
                    "timestamp_start": start_time.timestamp(),
                    "timestamp_end": end_time.timestamp(),
                    "session_duration_hours": 8.0,
                    "total_breaths": 1000,
                    "machine_events": [],
                    "mode_results": {
                        "aasm": {
                            "mode_name": "aasm",
                            "ahi": 5.0,
                            "rdi": 5.0,
                            "apneas": [],
                            "hypopneas": [],
                        }
                    },
                    "flow_analysis": {
                        "total_breaths": 1000,
                        "class_distribution": {
                            1: 500,
                            2: 200,
                            3: 100,
                            4: 100,
                            5: 50,
                            6: 30,
                            7: 20,
                        },
                        "flow_limitation_index": 0.25,
                        "average_confidence": 0.85,
                        "patterns": [],
                    },
                    "csr_detection": None,
                    "periodic_breathing": None,
                }

                analysis = models.AnalysisResult(
                    session_id=sess.id,
                    timestamp_start=start_time,
                    timestamp_end=end_time,
                    programmatic_result_json=analysis_json,
                    processing_time_ms=100,
                    engine_versions_json={"version": "1.0.0"},
                    created_at=start_time + timedelta(minutes=j),
                )
                session.add(analysis)
                session.flush()

                pattern = models.DetectedPattern(
                    analysis_result_id=analysis.id,
                    pattern_id="TEST_PATTERN",
                    start_time=start_time,
                    duration=8 * 3600,
                    confidence=0.95,
                    detected_by="programmatic",
                    metrics_json={"test": "pattern"},
                )
                session.add(pattern)

        session.commit()

    return temp_db


class TestAnalysisDeleteCommand:
    """Test analysis delete command with various scenarios."""

    def test_delete_analysis_single_session(self, cli_runner, db_with_analysis):
        """Test deleting analysis for a single session (latest only)."""
        with session_scope() as session:
            analysis_before = (
                session.query(models.AnalysisResult).filter_by(session_id=1).count()
            )
            assert analysis_before == 3

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "Successfully deleted 1 analysis record(s)" in result.output

        with session_scope() as session:
            analysis_after = (
                session.query(models.AnalysisResult).filter_by(session_id=1).count()
            )
            assert analysis_after == 2

            sess = session.query(models.Session).filter_by(id=1).first()
            assert sess is not None

    def test_delete_analysis_all_versions(self, cli_runner, db_with_analysis):
        """Test deleting all analysis versions for a session."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1",
                "--all-versions",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "Successfully deleted 3 analysis record(s)" in result.output

        with session_scope() as session:
            analysis_after = (
                session.query(models.AnalysisResult).filter_by(session_id=1).count()
            )
            assert analysis_after == 0

            sess = session.query(models.Session).filter_by(id=1).first()
            assert sess is not None

    def test_delete_analysis_multiple_sessions(self, cli_runner, db_with_analysis):
        """Test deleting analysis for multiple sessions."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1,2,3",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "Successfully deleted 3 analysis record(s)" in result.output
        assert "3 session(s)" in result.output

        with session_scope() as session:
            analysis_1 = (
                session.query(models.AnalysisResult).filter_by(session_id=1).count()
            )
            analysis_2 = (
                session.query(models.AnalysisResult).filter_by(session_id=2).count()
            )
            analysis_3 = (
                session.query(models.AnalysisResult).filter_by(session_id=3).count()
            )

            assert analysis_1 == 2
            assert analysis_2 == 2
            assert analysis_3 == 0

    def test_delete_analysis_date_range(self, cli_runner, db_with_analysis):
        """Test deleting analysis by date range."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--from",
                "2025-10-01",
                "--to",
                "2025-10-03",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "Successfully deleted" in result.output

        with session_scope() as session:
            total_analysis = session.query(models.AnalysisResult).count()
            assert total_analysis == 7

    def test_delete_analysis_dry_run(self, cli_runner, db_with_analysis):
        """Test dry-run mode doesn't actually delete."""
        with session_scope() as session:
            analysis_before = session.query(models.AnalysisResult).count()

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "DRY RUN MODE" in result.output
        assert "Dry run complete" in result.output

        with session_scope() as session:
            analysis_after = session.query(models.AnalysisResult).count()
            assert analysis_after == analysis_before

    def test_delete_analysis_cancellation(self, cli_runner, db_with_analysis):
        """Test that user can cancel deletion."""
        result = cli_runner.invoke(
            cli,
            ["analysis", "delete", "--db", str(db_with_analysis), "--session-id", "1"],
            input="n\n",
        )

        assert result.exit_code == 0
        assert "Deletion cancelled" in result.output

        with session_scope() as session:
            analysis = (
                session.query(models.AnalysisResult).filter_by(session_id=1).count()
            )
            assert analysis == 3

    def test_delete_analysis_no_filter_error(self, cli_runner, db_with_analysis):
        """Test that command errors when no filter is provided."""
        result = cli_runner.invoke(
            cli, ["analysis", "delete", "--db", str(db_with_analysis)]
        )

        assert result.exit_code == 1
        assert "must specify at least one filter" in result.output

    def test_delete_analysis_no_sessions_found(self, cli_runner, temp_db):
        """Test graceful handling when no sessions have analysis."""
        init_database(str(temp_db))

        with session_scope() as session:
            device = models.Device(
                manufacturer="Test",
                model="Test",
                serial_number="TEST",
            )
            session.add(device)
            session.flush()

            sess = models.Session(
                device_id=device.id,
                device_session_id="test_session_1",
                start_time=datetime(2025, 10, 1, 22, 0, 0),
                end_time=datetime(2025, 10, 2, 6, 0, 0),
                duration_seconds=8 * 3600,
            )
            session.add(sess)
            session.commit()

        result = cli_runner.invoke(
            cli,
            ["analysis", "delete", "--db", str(temp_db), "--session-id", "1"],
        )

        assert result.exit_code == 0
        assert "No sessions with analysis results found" in result.output

    def test_delete_analysis_cascades_to_patterns(self, cli_runner, db_with_analysis):
        """Test that deleting analysis cascades to detected patterns."""
        with session_scope() as session:
            analysis = (
                session.query(models.AnalysisResult)
                .filter_by(session_id=1)
                .order_by(models.AnalysisResult.created_at.desc())
                .first()
            )
            latest_analysis_id = analysis.id

            patterns_before = (
                session.query(models.DetectedPattern)
                .filter_by(analysis_result_id=latest_analysis_id)
                .count()
            )
            assert patterns_before > 0

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "delete",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1",
                "--force",
            ],
        )

        assert result.exit_code == 0

        with session_scope() as session:
            patterns_after = (
                session.query(models.DetectedPattern)
                .filter_by(analysis_result_id=latest_analysis_id)
                .count()
            )
            assert patterns_after == 0

    def test_delete_analysis_all_flag(self, cli_runner, db_with_analysis):
        """Test deleting all analysis results."""
        result = cli_runner.invoke(
            cli,
            ["analysis", "delete", "--db", str(db_with_analysis), "--all", "--force"],
        )

        assert result.exit_code == 0
        assert "Successfully deleted 5 analysis record(s)" in result.output
        assert "5 session(s)" in result.output

        with session_scope() as session:
            total_analysis = session.query(models.AnalysisResult).count()
            assert total_analysis == 4


class TestAnalysisCommand:
    """Test consolidated analysis command."""

    def test_analyze_missing_selection_flag(self, cli_runner, temp_db):
        """Test that analysis run requires at least one selection flag."""
        init_database(str(temp_db))

        result = cli_runner.invoke(
            cli,
            ["analysis", "run", "--db", str(temp_db)],
        )

        assert result.exit_code == 1
        assert "Must provide at least one selection flag" in result.output

    def test_analyze_mutually_exclusive_single_flags(self, cli_runner, temp_db):
        """Test that --session-id and --date are mutually exclusive."""
        init_database(str(temp_db))

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "run",
                "--db",
                str(temp_db),
                "--session-id",
                "1",
                "--date",
                "2025-01-01",
            ],
        )

        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_analyze_mutually_exclusive_single_and_batch(self, cli_runner, temp_db):
        """Test that single session flags cannot be used with batch flags."""
        init_database(str(temp_db))

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "run",
                "--db",
                str(temp_db),
                "--session-id",
                "1",
                "--from",
                "2025-01-01",
            ],
        )

        assert result.exit_code == 1
        assert "cannot be used with batch flags" in result.output

    def test_analyze_list_mode(self, cli_runner, db_with_analysis):
        """Test list subcommand shows analysis status."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "list",
                "--db",
                str(db_with_analysis),
            ],
        )

        assert result.exit_code == 0
        assert "Date" in result.output
        assert "Analyzed" in result.output

    def test_analyze_list_with_date_range(self, cli_runner, db_with_analysis):
        """Test list subcommand with date range filtering."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "list",
                "--db",
                str(db_with_analysis),
                "--from",
                "2025-10-01",
                "--to",
                "2025-10-03",
            ],
        )

        assert result.exit_code == 0

    def test_analyze_no_subcommand_shows_help(self, cli_runner, temp_db):
        """Test that running 'analyze' without subcommand shows help."""
        init_database(str(temp_db))

        result = cli_runner.invoke(cli, ["analysis"])

        assert result.exit_code in [0, 2]
        assert "Commands:" in result.output or "show" in result.output

    def test_analyze_show_by_session_id(self, cli_runner, db_with_analysis):
        """Test show subcommand displays stored analysis by session ID."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "show",
                "--db",
                str(db_with_analysis),
                "--session-id",
                "1",
            ],
        )

        assert result.exit_code == 0
        assert "Displaying stored analysis" in result.output
        assert "ANALYSIS SUMMARY" in result.output

    def test_analyze_show_by_date(self, cli_runner, db_with_analysis):
        """Test show subcommand displays stored analysis by date."""
        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "show",
                "--db",
                str(db_with_analysis),
                "--date",
                "2025-10-01",
            ],
        )

        assert result.exit_code == 0
        assert "Displaying stored analysis" in result.output
        assert "ANALYSIS SUMMARY" in result.output

    def test_analyze_show_no_analysis_found(self, cli_runner, temp_db):
        """Test show subcommand gracefully handles missing analysis."""
        init_database(str(temp_db))

        with session_scope() as session:
            device = models.Device(
                manufacturer="Test",
                model="Test",
                serial_number="TEST",
            )
            session.add(device)
            session.flush()

            sess = models.Session(
                device_id=device.id,
                device_session_id="test_session_1",
                start_time=datetime(2025, 10, 1, 22, 0, 0),
                end_time=datetime(2025, 10, 2, 6, 0, 0),
                duration_seconds=8 * 3600,
            )
            session.add(sess)
            session.commit()

        result = cli_runner.invoke(
            cli,
            [
                "analysis",
                "show",
                "--db",
                str(temp_db),
                "--session-id",
                "1",
            ],
        )

        assert result.exit_code == 1
        assert "No analysis found" in result.output


class TestDbDropCommand:
    """Test db drop command - focused on critical behavior."""

    def test_db_drop_deletes_database(self, cli_runner, populated_test_db):
        """Test that drop command actually deletes the database and associated files."""
        assert populated_test_db.exists()

        result = cli_runner.invoke(
            cli,
            ["db", "drop", "--db", str(populated_test_db)],
            input="y\n",
        )

        assert result.exit_code == 0

        assert not populated_test_db.exists()
        assert not Path(str(populated_test_db) + "-wal").exists()
        assert not Path(str(populated_test_db) + "-shm").exists()

    def test_db_drop_force_flag(self, cli_runner, populated_test_db):
        """Test that --force flag skips confirmation."""
        result = cli_runner.invoke(
            cli,
            ["db", "drop", "--db", str(populated_test_db), "--force"],
        )

        assert result.exit_code == 0
        assert not populated_test_db.exists()


class TestDbInitCommand:
    """Test db init command - focused on critical behavior."""

    def test_db_init_creates_database(self, cli_runner, temp_db):
        """Test that init creates a functional database."""
        result = cli_runner.invoke(
            cli,
            ["db", "init", "--db", str(temp_db)],
        )

        assert result.exit_code == 0
        assert temp_db.exists()

        with session_scope() as session:
            device_count = session.query(models.Device).count()
            assert device_count == 0

        result2 = cli_runner.invoke(
            cli,
            ["db", "init", "--db", str(temp_db)],
        )

        assert result2.exit_code == 0
        assert temp_db.exists()


class TestSessionShowCommand:
    """Tests for session show command."""

    @pytest.mark.integration
    def test_session_show_settings_flag(
        self, temp_db, resmed_parser, resmed_fixture_path
    ):
        """Test --settings flag displays settings."""
        from snore.database.importers import SessionImporter

        init_database(str(temp_db))

        sessions = list(resmed_parser.parse_sessions(resmed_fixture_path, limit=1))
        importer = SessionImporter()
        importer.import_session(sessions[0])

        runner = CliRunner()
        result = runner.invoke(
            cli, ["session", "show", "1", "--settings", "--db", str(temp_db)]
        )

        assert result.exit_code == 0
        assert "Settings:" in result.output
        assert "mode:" in result.output


class TestWaveformListCommand:
    """Test waveform list command."""

    def test_waveform_list_shows_types(self, cli_runner, populated_test_db_full):
        """Test waveform list displays available types."""
        result = cli_runner.invoke(
            cli,
            [
                "waveform",
                "list",
                "--db",
                str(populated_test_db_full),
                "--session-id",
                "1",
            ],
        )

        assert result.exit_code == 0
        assert "flow" in result.output
        assert "pressure" in result.output
        assert "leak" in result.output

    def test_waveform_list_no_session(self, cli_runner, populated_test_db_full):
        """Test waveform list errors when session not specified."""
        result = cli_runner.invoke(
            cli,
            ["waveform", "list", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code != 0


class TestSessionShowExpanded:
    """Test expanded session show output with full statistics."""

    def test_session_show_displays_full_stats(self, cli_runner, populated_test_db_full):
        """Test session show displays comprehensive statistics."""
        result = cli_runner.invoke(
            cli,
            ["session", "show", "1", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code == 0
        assert "AHI:" in result.output
        assert "REI:" in result.output
        assert "Pressure:" in result.output
        assert "Leak:" in result.output
        assert "SpO₂:" in result.output or "SpO2:" in result.output
        assert "Pulse:" in result.output
        assert "Respiratory:" in result.output or "Respiratory Rate:" in result.output

    def test_session_show_displays_waveform_types(
        self, cli_runner, populated_test_db_full
    ):
        """Test session show lists available waveform types."""
        result = cli_runner.invoke(
            cli,
            ["session", "show", "1", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code == 0
        assert "flow" in result.output
        assert "pressure" in result.output
        assert "leak" in result.output


class TestStatsEnhanced:
    """Test enhanced stats command with respiratory, pulse, and SpO2 detail."""

    def test_stats_shows_respiratory(self, cli_runner, populated_test_db_full):
        """Test stats displays respiratory metrics."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code == 0
        assert "Respiratory Rate:" in result.output or "Respiratory" in result.output
        assert "Tidal Volume:" in result.output or "Tidal" in result.output
        assert "Minute Ventilation:" in result.output or "Ventilation" in result.output

    def test_stats_shows_pulse(self, cli_runner, populated_test_db_full):
        """Test stats displays pulse section."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code == 0
        assert "Pulse" in result.output

    def test_stats_shows_spo2_below_90(self, cli_runner, populated_test_db_full):
        """Test stats displays time below 90% SpO2."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full)],
        )

        assert result.exit_code == 0
        assert "Time below 90%:" in result.output or "below 90" in result.output


class TestStatsPeriod:
    """Test stats command with period breakdown."""

    def test_stats_period_month(self, cli_runner, populated_test_db_full):
        """Test stats with monthly period breakdown."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full), "--period", "month"],
        )

        assert result.exit_code == 0
        assert "Oct 2025" in result.output or "2025-10" in result.output

    def test_stats_period_week(self, cli_runner, populated_test_db_full):
        """Test stats with weekly period breakdown."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full), "--period", "week"],
        )

        assert result.exit_code == 0


class TestStatsTrend:
    """Test stats command with trend visualization."""

    def test_stats_trend_defaults_to_week(self, cli_runner, populated_test_db_full):
        """Test stats with trend flag defaults to weekly periods."""
        result = cli_runner.invoke(
            cli,
            ["stats", "--db", str(populated_test_db_full), "--trend"],
        )

        assert result.exit_code == 0

    def test_stats_trend_with_period(self, cli_runner, populated_test_db_full):
        """Test stats with trend and custom period."""
        result = cli_runner.invoke(
            cli,
            [
                "stats",
                "--db",
                str(populated_test_db_full),
                "--period",
                "month",
                "--trend",
            ],
        )

        assert result.exit_code == 0
