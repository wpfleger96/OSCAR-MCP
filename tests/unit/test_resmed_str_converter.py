"""Unit tests for ResmedEDFParser STR.edf conversion helpers."""

from datetime import date, datetime

import pytest

from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.unified import TherapyMode


class TestTherapyDateHelper:
    """Tests for _therapy_date — ResMed noon-to-noon day convention."""

    def test_therapy_date_after_midnight_maps_to_previous_day(self):
        """Session starting at 01:11 belongs to the therapy night of the day before."""
        assert ResmedEDFParser._therapy_date(datetime(2026, 5, 28, 1, 11)) == date(
            2026, 5, 27
        )

    def test_therapy_date_before_midnight_maps_to_same_day(self):
        """Session starting at 23:50 belongs to the therapy night that started that day."""
        assert ResmedEDFParser._therapy_date(datetime(2026, 5, 27, 23, 50)) == date(
            2026, 5, 27
        )

    def test_therapy_date_at_noon_maps_to_same_day(self):
        """Noon exactly is the start of the new therapy day — maps to that day."""
        assert ResmedEDFParser._therapy_date(datetime(2026, 5, 27, 12, 0)) == date(
            2026, 5, 27
        )

    def test_therapy_date_one_minute_before_noon_maps_to_previous_day(self):
        """11:59 is still within the previous day's therapy window."""
        assert ResmedEDFParser._therapy_date(datetime(2026, 5, 27, 11, 59)) == date(
            2026, 5, 26
        )


class TestConvertStrToTherapySettings:
    """Tests for _convert_str_to_therapy_settings sentinel detection and field mapping."""

    @pytest.fixture
    def parser(self):
        return ResmedEDFParser()

    def test_all_sentinel_values_returns_none(self, parser):
        """All-negative dict (no-usage day) must return None, not a degenerate TherapySettings."""
        sentinel_record = {
            "mode": -1.0,
            "pressure_fixed": -0.02,
            "pressure_min": -0.02,
            "pressure_max": -0.02,
            "epr_level": -0.02,
            "epr_mode": -1.0,
            "climate_control": -1.0,
            "humidity_enabled": -1.0,
            "humidity_level": -1.0,
            "smart_start": -1.0,
            "ab_filter": -1.0,
            "mask_type": -1.0,
            "tube_temp": -0.1,
        }
        assert parser._convert_str_to_therapy_settings(sentinel_record) is None

    def test_valid_record_returns_therapy_settings(self, parser):
        """Valid record (record 0 from fixture) must return a populated TherapySettings."""
        valid_record = {
            "mode": 1.0,
            "pressure_fixed": 10.0,
            "epr_level": 2.0,
            "epr_mode": 2.0,
            "climate_control": 1.0,
            "humidity_enabled": 2.0,
            "humidity_level": 4.0,
            "smart_start": 2.0,
            "ab_filter": 1.0,
            "mask_type": 4.0,
            "tube_temp": 27.0,
        }
        settings = parser._convert_str_to_therapy_settings(valid_record)
        assert settings is not None
        assert settings.mode == TherapyMode.APAP
        assert settings.pressure_fixed == 10.0
        assert settings.epr_level == 2
        assert settings.epr_mode == "Full Time"
        assert settings.climate_control == "Manual"
        assert settings.humidity_enabled is True
        assert settings.humidity_level == 4
        assert settings.smart_start is True
        assert settings.ab_filter == "Antibacterial"
        assert settings.mask_type == "Nasal"
        assert settings.tube_temp == 27.0

    def test_mixed_record_returns_settings_with_sentinel_fields_as_none(self, parser):
        """Dict with some sentinels and some valid values must not be discarded.

        Fields backed by sentinel values become None; fields with valid values
        are converted normally.  The all-sentinel fast path must not fire.
        """
        mixed_record = {
            "mode": 1.0,
            "pressure_fixed": -0.02,  # sentinel — should become None
            "epr_level": 2.0,
        }
        settings = parser._convert_str_to_therapy_settings(mixed_record)
        assert settings is not None
        assert settings.mode == TherapyMode.APAP
        assert settings.pressure_fixed is None
        assert settings.epr_level == 2
