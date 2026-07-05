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

    def test_valid_cpap_record_returns_therapy_settings(self, parser):
        """Valid Series 10 CPAP record returns a populated TherapySettings.

        Mask decoding passes is_eleven_series=True: the raw mask_type value 4.0
        is an 11-series code (2–4 scale); after the -2 shift it maps to 2 → "Nasal".
        """
        parser._str_series11 = False  # Series 10: mode 0 = CPAP
        valid_record = {
            "mode": 0.0,
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
        assert settings.mode == TherapyMode.CPAP
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
        parser._str_series11 = False  # Series 10: mode 1 = APAP
        mixed_record = {
            "mode": 1.0,
            "pressure_min": -0.02,  # sentinel — should become None
            "pressure_max": 20.0,
            "epr_level": 2.0,
        }
        settings = parser._convert_str_to_therapy_settings(mixed_record)
        assert settings is not None
        assert settings.mode == TherapyMode.APAP
        assert settings.pressure_min is None
        assert settings.pressure_max == 20.0
        assert settings.epr_level == 2

    def test_series11_vauto_mode8_maps_bipap_auto(self, parser):
        """Series 11 mode 8 with VA signals maps to BIPAP_AUTO with correct bilevel pressures."""
        parser._str_series11 = True
        record = {
            "mode": 8.0,
            # Stale CPAP/APAP presets that should NOT leak through
            "pressure_fixed": 10.0,
            "pressure_min": 4.0,
            "pressure_max": 20.0,
            "epr_level": 1.0,
            "epr_mode": 2.0,
            # Active VAuto signals
            "va_min_epap": 5.0,
            "va_max_ipap": 14.0,
            "va_ps": 3.0,
            "va_start_press": 5.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.BIPAP_AUTO
        assert settings.epap == 5.0
        assert settings.ipap == 14.0
        assert settings.ps == 3.0
        # Dormant presets must not appear
        assert settings.pressure_fixed is None
        assert settings.pressure_min is None
        assert settings.pressure_max is None
        assert settings.epr_level is None
        assert settings.epr_mode is None

    def test_vauto_ti_trigger_cycle_land_in_other_settings(self, parser):
        """VAuto timing parameters are stored as strings in other_settings."""
        parser._str_series11 = True
        record = {
            "mode": 8.0,
            "va_min_epap": 5.0,
            "va_max_ipap": 14.0,
            "va_ps": 3.0,
            "va_ti_max": 2.5,
            "va_ti_min": 0.3,
            "va_trigger": 3.0,
            "va_cycle": 1.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.other_settings["ti_max"] == "2.5"
        assert settings.other_settings["ti_min"] == "0.3"
        assert settings.other_settings["trigger"] == "3"
        assert settings.other_settings["cycle"] == "1"

    def test_series11_mode3_cpap_has_pressure_fixed_and_epr(self, parser):
        """Series 11 mode 3 → CPAP with pressure_fixed and EPR; no bilevel fields."""
        parser._str_series11 = True
        record = {
            "mode": 3.0,
            "pressure_fixed": 9.0,
            "epr_level": 1.0,
            "epr_mode": 2.0,
            # Stale VAuto keys that must not leak through
            "va_min_epap": 4.0,
            "va_max_ipap": 12.0,
            "va_ps": 2.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.CPAP
        assert settings.pressure_fixed == 9.0
        assert settings.epr_level == 1
        assert settings.epr_mode == "Full Time"
        assert settings.ipap is None
        assert settings.epap is None

    def test_series11_mode1_apap_pressure_min_max(self, parser):
        """Series 11 mode 1 → APAP with pressure_min/pressure_max."""
        parser._str_series11 = True
        record = {
            "mode": 1.0,
            "pressure_min": 4.0,
            "pressure_max": 20.0,
            "ramp_start_pressure": 4.0,
            "epr_level": 2.0,
            "epr_mode": 2.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.APAP
        assert settings.pressure_min == 4.0
        assert settings.pressure_max == 20.0
        assert settings.pressure_fixed is None

    def test_series11_mode2_apap_via_afh_signals(self, parser):
        """Series 11 mode 2 (A4Her) falls back to AFH signals for pressure range."""
        parser._str_series11 = True
        record = {
            "mode": 2.0,
            "afh_min_press": 5.0,
            "afh_max_press": 18.0,
            "afh_start_press": 5.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.APAP
        assert settings.pressure_min == 5.0
        assert settings.pressure_max == 18.0
        assert settings.ramp_start_pressure == 5.0

    def test_unknown_mode_series11_returns_none(self, parser):
        """Unmapped mode value on Series 11 returns None (never fabricates CPAP)."""
        parser._str_series11 = True
        record = {"mode": 5.0, "pressure_fixed": 10.0}
        assert parser._convert_str_to_therapy_settings(record) is None

    def test_unknown_mode_series10_returns_none(self, parser):
        """Unmapped mode value on Series 10 returns None."""
        parser._str_series11 = False
        record = {"mode": 99.0, "pressure_fixed": 10.0}
        assert parser._convert_str_to_therapy_settings(record) is None

    def test_missing_mode_key_returns_none(self, parser):
        """Missing mode key returns None; a settings record without a mode is unusable."""
        record = {"pressure_fixed": 10.0, "humidity_level": 4.0}
        assert parser._convert_str_to_therapy_settings(record) is None

    def test_series10_mode6_maps_bipap_auto(self, parser):
        """Series 10 mode 6 (VPAP Auto / VAuto) maps to BIPAP_AUTO."""
        parser._str_series11 = False
        record = {
            "mode": 6.0,
            "va_min_epap": 6.0,
            "va_max_ipap": 15.0,
            "va_ps": 4.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.BIPAP_AUTO
        assert settings.epap == 6.0
        assert settings.ipap == 15.0

    def test_series10_mode3_bipap_computes_ps(self, parser):
        """Series 10 mode 3 (VPAP S) maps to BIPAP with computed ps = ipap - epap."""
        parser._str_series11 = False
        record = {
            "mode": 3.0,
            "s_ipap": 14.0,
            "s_epap": 8.0,
            "s_start_press": 8.0,
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.mode == TherapyMode.BIPAP
        assert settings.ipap == 14.0
        assert settings.epap == 8.0
        assert settings.ps == 6.0

    def test_vauto_ps_sentinel_becomes_none(self, parser):
        """VAuto ps sentinel value (-0.02) results in ps=None."""
        parser._str_series11 = True
        record = {
            "mode": 8.0,
            "va_min_epap": 5.0,
            "va_max_ipap": 14.0,
            "va_ps": -0.02,  # sentinel
        }
        settings = parser._convert_str_to_therapy_settings(record)
        assert settings is not None
        assert settings.ps is None


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
