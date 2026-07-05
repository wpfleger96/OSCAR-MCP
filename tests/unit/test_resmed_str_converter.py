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
        """Valid record (record 0 from fixture) must return a populated TherapySettings.

        The fixture uses is_eleven_series=True because the raw mask_type value is 4.0,
        which is an 11-series raw code (2–4 scale); after the -2 shift it maps to 2 → "Nasal".
        """
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
        settings = parser._convert_str_to_therapy_settings(
            valid_record, is_eleven_series=True
        )
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


class TestMaskTypeMapping:
    """Tests for generation-aware mask code decoding."""

    @pytest.fixture
    def parser(self):
        return ResmedEDFParser()

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (0, "Pillows"),
            (1, "Full Face"),
            (2, "Nasal"),
            (3, "Unknown"),
            (4, "Unknown"),
            (5, "Unknown"),
        ],
    )
    def test_ten_series_mask_codes(self, parser, raw, expected):
        """S9/10-series codes 0–2 map to mask names; anything else → Unknown."""
        values = {"mode": 1.0, "mask_type": float(raw)}
        settings = parser._convert_str_to_therapy_settings(
            values, is_eleven_series=False
        )
        assert settings is not None
        assert settings.mask_type == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (2, "Pillows"),
            (3, "Full Face"),
            (4, "Nasal"),
            (0, "Unknown"),
            (1, "Unknown"),
            (5, "Unknown"),
        ],
    )
    def test_eleven_series_mask_codes(self, parser, raw, expected):
        """11-series raw codes 2–4 are shifted down by 2 before lookup; others → Unknown."""
        values = {"mode": 1.0, "mask_type": float(raw)}
        settings = parser._convert_str_to_therapy_settings(
            values, is_eleven_series=True
        )
        assert settings is not None
        assert settings.mask_type == expected


class TestIsElevenSeries:
    """Tests for _is_eleven_series model-string detection."""

    @pytest.mark.parametrize(
        "model, expected",
        [
            (
                "AirSense11AutoSet",
                True,
            ),  # space-less ProductName from Identification.json
            ("AirCurve 11 VAuto", True),
            ("AirSense 10 AutoSet", False),
            ("AirCurve 10 VAuto", False),
            ("S9 AutoSet", False),
            ("Unknown", False),
            ("", False),
        ],
    )
    def test_model_detection(self, model, expected):
        assert ResmedEDFParser._is_eleven_series(model) is expected
