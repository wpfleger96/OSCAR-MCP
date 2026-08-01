"""Tests for STR.edf settings parsing."""

from datetime import date

import pytest

from snore.parsers.unified import TherapyMode


class TestSTRSettingsParsing:
    """Tests for parsing settings from STR.edf."""

    @pytest.mark.parser
    def test_str_file_exists(self, resmed_fixture_path):
        """Test that STR.edf fixture exists."""
        str_file = resmed_fixture_path / "STR.edf"
        assert str_file.exists(), f"STR.edf not found at {str_file}"

    @pytest.mark.parser
    def test_parse_str_settings_returns_therapy_settings(
        self, resmed_parser, resmed_fixture_path
    ):
        """Test that STR.edf parsing returns TherapySettings object."""
        str_file = resmed_fixture_path / "STR.edf"

        session_date = date(2023, 8, 22)
        settings = resmed_parser._parse_str_settings(str_file, session_date)

        assert settings is not None, "Settings should be parsed from STR.edf"
        assert settings.mode is not None, "Mode should be set"
        assert isinstance(settings.mode, TherapyMode), "Mode should be TherapyMode enum"

    @pytest.mark.parser
    def test_parse_str_settings_pressure_values(
        self, resmed_parser, resmed_fixture_path
    ):
        """Test pressure settings are correctly parsed."""
        str_file = resmed_fixture_path / "STR.edf"
        session_date = date(2023, 8, 22)
        settings = resmed_parser._parse_str_settings(str_file, session_date)

        assert settings is not None

        if settings.pressure_min is not None:
            assert 4.0 <= settings.pressure_min <= 20.0, (
                f"Min pressure out of range: {settings.pressure_min}"
            )

        if settings.pressure_max is not None:
            assert 4.0 <= settings.pressure_max <= 20.0, (
                f"Max pressure out of range: {settings.pressure_max}"
            )

        if settings.pressure_fixed is not None:
            assert 4.0 <= settings.pressure_fixed <= 20.0, (
                f"Fixed pressure out of range: {settings.pressure_fixed}"
            )

        if settings.pressure_min and settings.pressure_max:
            assert settings.pressure_min <= settings.pressure_max, (
                "Min pressure should be less than or equal to max pressure"
            )

    @pytest.mark.parser
    def test_parse_str_settings_epr(self, resmed_parser, resmed_fixture_path):
        """Test EPR settings are correctly parsed."""
        str_file = resmed_fixture_path / "STR.edf"
        session_date = date(2023, 8, 22)
        settings = resmed_parser._parse_str_settings(str_file, session_date)

        assert settings is not None

        if settings.epr_level is not None:
            assert 0 <= settings.epr_level <= 3, (
                f"EPR level out of range: {settings.epr_level}"
            )

        if settings.epr_mode is not None:
            assert settings.epr_mode in ["Off", "Ramp Only", "Full Time", "Unknown"], (
                f"Invalid EPR mode: {settings.epr_mode}"
            )

    @pytest.mark.parser
    def test_parse_str_settings_climate_control(
        self, resmed_parser, resmed_fixture_path
    ):
        """Test climate control settings are correctly parsed."""
        str_file = resmed_fixture_path / "STR.edf"
        session_date = date(2023, 8, 22)
        settings = resmed_parser._parse_str_settings(str_file, session_date)

        assert settings is not None

        if settings.humidity_level is not None:
            assert 0 <= settings.humidity_level <= 8, (
                f"Humidity level out of range: {settings.humidity_level}"
            )

        if settings.tube_temp is not None:
            assert 16 <= settings.tube_temp <= 30, (
                f"Tube temp out of range: {settings.tube_temp}"
            )

        if settings.climate_control is not None:
            assert settings.climate_control in ["Manual", "Auto"], (
                f"Invalid climate control: {settings.climate_control}"
            )

    @pytest.mark.parser
    @pytest.mark.integration
    def test_session_has_settings_after_parse(self, resmed_parser, resmed_fixture_path):
        """Test that parsed sessions have settings populated."""
        sessions = list(resmed_parser.parse_sessions(resmed_fixture_path, limit=1))
        assert len(sessions) > 0, "Should parse at least one session"

        session = sessions[0]
        assert session.settings is not None, "Session should have settings"
        assert session.settings.mode is not None, "Settings should have mode"

    @pytest.mark.parser
    @pytest.mark.integration
    async def test_settings_imported_to_database(
        self, temp_db, resmed_parser, resmed_fixture_path
    ):
        """Test that settings are stored in database after import."""
        from sqlalchemy import select

        from snore.database import models
        from snore.database.importers import import_session
        from snore.database.session import init_database, session_scope

        init_database(str(temp_db))

        sessions = list(resmed_parser.parse_sessions(resmed_fixture_path, limit=1))
        assert len(sessions) > 0, "Should parse at least one session"

        session = sessions[0]
        assert session.settings is not None, "Session should have settings"
        await import_session(session)

        async with session_scope() as db:
            settings = (await db.execute(select(models.Setting))).scalars().all()
            assert len(settings) > 0, "Settings should be imported to database"

            keys = {s.key for s in settings}
            assert "mode" in keys, "Mode setting should be imported"

    @pytest.mark.parser
    def test_date_out_of_range_returns_none(self, resmed_parser, resmed_fixture_path):
        """Test that date outside STR.edf range returns None."""
        str_file = resmed_fixture_path / "STR.edf"

        old_date = date(2000, 1, 1)
        settings = resmed_parser._parse_str_settings(str_file, old_date)

        assert settings is None, "Settings should be None for date out of range"

    @pytest.mark.parser
    def test_str_settings_signal_labels(self, resmed_parser, resmed_fixture_path):
        """Test that STR.edf has expected signal labels."""
        from snore.parsers.formats.edf import EDFReader

        str_file = resmed_fixture_path / "STR.edf"

        with EDFReader(str_file) as edf:
            signals = edf.list_signal_labels()
            assert len(signals) > 0, "STR.edf should have signals"

            expected_signals = [
                "S.A.MinPress",
                "S.A.MaxPress",
                "S.EPR.Level",
                "S.HumLevel",
            ]

            found_signals = [sig for sig in expected_signals if sig in signals]
            assert len(found_signals) > 0, (
                f"Should find some expected signals. Found: {signals}"
            )

    @pytest.mark.parser
    def test_no_usage_date_returns_none(self, resmed_parser, resmed_fixture_path):
        """_parse_str_settings must return None for a no-usage sentinel day.

        Record 1 (2023-08-23) in the fixture has all-negative sentinel values;
        it must not produce a degenerate TherapySettings object.
        """
        str_file = resmed_fixture_path / "STR.edf"
        settings = resmed_parser._parse_str_settings(str_file, date(2023, 8, 23))
        assert settings is None, (
            "Settings should be None for a no-usage day with sentinel values"
        )

    @pytest.mark.parser
    def test_first_valid_date_returns_settings(
        self, resmed_parser, resmed_fixture_path
    ):
        """_parse_str_settings must return real settings for the first valid fixture day.

        Record 0 (2023-08-22) has Mode=1.0 (APAP), S.C.Press=10.0, etc.
        """
        str_file = resmed_fixture_path / "STR.edf"
        settings = resmed_parser._parse_str_settings(str_file, date(2023, 8, 22))
        assert settings is not None, "Settings should be parsed for the first valid day"
        assert settings.mode is not None
        from snore.parsers.unified import TherapyMode

        assert isinstance(settings.mode, TherapyMode)
