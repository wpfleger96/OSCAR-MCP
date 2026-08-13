"""Unit tests for the Health Auto Export (HAE) JSON payload parser.

All tests are synchronous — no database access required.
"""

from __future__ import annotations

import json

from datetime import date, datetime
from pathlib import Path

import pytest

from snore.parsers.apple_health.hae_json import parse_payload
from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.type_handlers import (
    HAE_METRIC_NAME_MAP,
    SLEEP_TYPE,
    parse_hae_metric,
)

FIXTURE_JSON = (
    Path(__file__).parent.parent / "fixtures" / "health_data" / "hae_payload.json"
)


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_JSON.read_text())


class TestEnvelopeWalk:
    """Top-level payload structure is parsed correctly."""

    def test_fixture_yields_records(self) -> None:
        """Parsing the fixture produces at least one record."""
        result = parse_payload(_load_fixture())
        assert len(result.records) > 0

    def test_fixture_has_unknown_metric(self) -> None:
        """step_count in the fixture is an unknown metric (stored under its original name)."""
        result = parse_payload(_load_fixture())
        assert "step_count" in result.unknown_metrics

    def test_fixture_has_skipped_points(self) -> None:
        """The malformed {} point in the fixture is counted as skipped."""
        result = parse_payload(_load_fixture())
        assert result.skipped_points >= 1

    def test_ingest_channel_is_hae_json(self) -> None:
        """All records from parse_payload carry ingest_channel='hae_json'."""
        result = parse_payload(_load_fixture())
        for rec in result.records:
            assert rec.ingest_channel == "hae_json"


class TestMissingEnvelopeKeys:
    """Tolerant behaviour on missing / wrong-type keys."""

    def test_empty_dict_returns_empty_result(self) -> None:
        result = parse_payload({})
        assert result.records == []
        assert result.unknown_metrics == {}
        assert result.skipped_points == 0

    def test_missing_data_key_returns_empty(self) -> None:
        result = parse_payload({"foo": "bar"})
        assert result.records == []

    def test_missing_metrics_key_returns_empty(self) -> None:
        result = parse_payload({"data": {}})
        assert result.records == []

    def test_metrics_not_a_list_returns_empty(self) -> None:
        result = parse_payload({"data": {"metrics": "bad"}})
        assert result.records == []


class TestSleepValueMapping:
    """HAE sleep vocabulary → canonical stage names."""

    def _sleep_metric(self, points: list[dict[str, object]]) -> dict[str, object]:
        return {"name": "Sleep Analysis", "units": "hr", "data": points}

    def _parse_sleep(self, points: list[dict[str, object]]) -> list[RawHealthRecord]:
        return list(parse_hae_metric(self._sleep_metric(points)))

    def test_in_bed_maps_to_inbed(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-15 23:00:00 -0500",
                    "endDate": "2024-01-16 00:00:00 -0500",
                    "value": "In Bed",
                }
            ]
        )
        assert len(recs) == 1
        assert recs[0].value_text == "InBed"

    def test_asleep_maps_to_unspecified(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-16 00:00:00 -0500",
                    "endDate": "2024-01-16 01:00:00 -0500",
                    "value": "Asleep",
                }
            ]
        )
        assert recs[0].value_text == "AsleepUnspecified"

    def test_core_maps_to_asleepcore(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-16 01:00:00 -0500",
                    "endDate": "2024-01-16 02:00:00 -0500",
                    "value": "Core",
                }
            ]
        )
        assert recs[0].value_text == "AsleepCore"

    def test_deep_maps_to_asleepdeep(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-16 02:00:00 -0500",
                    "endDate": "2024-01-16 03:00:00 -0500",
                    "value": "Deep",
                }
            ]
        )
        assert recs[0].value_text == "AsleepDeep"

    def test_rem_maps_to_asleeprem(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-16 03:00:00 -0500",
                    "endDate": "2024-01-16 04:00:00 -0500",
                    "value": "REM",
                }
            ]
        )
        assert recs[0].value_text == "AsleepREM"

    def test_awake_maps_to_awake(self) -> None:
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-16 04:00:00 -0500",
                    "endDate": "2024-01-16 04:15:00 -0500",
                    "value": "Awake",
                }
            ]
        )
        assert recs[0].value_text == "Awake"

    def test_sleep_records_have_no_value_num(self) -> None:
        """Sleep records must have value_num == None."""
        recs = self._parse_sleep(
            [
                {
                    "startDate": "2024-01-15 23:00:00 -0500",
                    "endDate": "2024-01-16 00:00:00 -0500",
                    "value": "Core",
                }
            ]
        )
        assert recs[0].value_num is None


class TestMetricNameMapping:
    """HAE metric names map to correct HK identifiers."""

    def test_sleep_analysis_maps_to_sleep_type(self) -> None:
        """Map is keyed by snake_case."""
        assert HAE_METRIC_NAME_MAP["sleep_analysis"] == SLEEP_TYPE

    def test_blood_oxygen_maps_to_hk_identifier(self) -> None:
        assert (
            HAE_METRIC_NAME_MAP["blood_oxygen_saturation"]
            == "HKQuantityTypeIdentifierOxygenSaturation"
        )

    def test_respiratory_rate_maps_to_hk_identifier(self) -> None:
        assert (
            HAE_METRIC_NAME_MAP["respiratory_rate"]
            == "HKQuantityTypeIdentifierRespiratoryRate"
        )

    def test_snake_case_and_display_string_resolve_identically(self) -> None:
        """Both 'sleep_analysis' (REST form) and 'Sleep Analysis' (display form) yield records."""
        point: dict[str, object] = {
            "startDate": "2024-01-16 00:00:00 -0500",
            "endDate": "2024-01-16 01:00:00 -0500",
            "value": "Core",
        }
        for name in ("sleep_analysis", "Sleep Analysis"):
            metric: dict[str, object] = {"name": name, "units": "hr", "data": [point]}
            recs = list(parse_hae_metric(metric))
            assert len(recs) == 1, f"No record produced for metric name {name!r}"
            assert recs[0].record_type == SLEEP_TYPE

    def test_display_string_resolves_via_parse_payload(self) -> None:
        """parse_payload also accepts display-string metric names."""
        payload: dict[str, object] = {
            "data": {
                "metrics": [
                    {
                        "name": "Blood Oxygen Saturation",
                        "units": "%",
                        "data": [{"date": "2024-01-16 02:30:00 -0500", "qty": 0.95}],
                    }
                ]
            }
        }
        result = parse_payload(payload)
        assert len(result.records) == 1
        assert (
            result.records[0].record_type == "HKQuantityTypeIdentifierOxygenSaturation"
        )
        assert result.skipped_points == 0


class TestQuantityPointParsing:
    """Quantity metric point shapes."""

    def _qty_metric(
        self,
        name: str,
        units: str,
        points: list[dict[str, object]],
    ) -> dict[str, object]:
        return {"name": name, "units": units, "data": points}

    def test_quantity_point_single_date_start_equals_end(self) -> None:
        """A quantity point with a single `date` field sets start_time == end_time."""
        metric = self._qty_metric(
            "Blood Oxygen Saturation",
            "%",
            [{"date": "2024-01-16 02:30:00 -0500", "qty": 0.95}],
        )
        recs = list(parse_hae_metric(metric))
        assert len(recs) == 1
        assert recs[0].start_time == recs[0].end_time
        assert recs[0].start_time == datetime(2024, 1, 16, 2, 30, 0)

    def test_qty_field_stored_as_value_num(self) -> None:
        metric = self._qty_metric(
            "Blood Oxygen Saturation",
            "%",
            [{"date": "2024-01-16 02:30:00 -0500", "qty": 0.962}],
        )
        recs = list(parse_hae_metric(metric))
        assert recs[0].value_num == pytest.approx(0.962, abs=1e-6)

    def test_min_avg_max_uses_avg(self) -> None:
        """When qty is absent but Min/Avg/Max present, Avg is used."""
        metric = self._qty_metric(
            "Respiratory Rate",
            "count/min",
            [
                {
                    "date": "2024-01-16 04:00:00 -0500",
                    "Min": 13.5,
                    "Avg": 15.8,
                    "Max": 18.2,
                }
            ],
        )
        recs = list(parse_hae_metric(metric))
        assert len(recs) == 1
        assert recs[0].value_num == pytest.approx(15.8, abs=1e-6)

    def test_value_num_rounded_to_4dp(self) -> None:
        metric = self._qty_metric(
            "Blood Oxygen Saturation",
            "%",
            [{"date": "2024-01-16 02:30:00 -0500", "qty": 0.96200000001}],
        )
        recs = list(parse_hae_metric(metric))
        assert recs[0].value_num == round(recs[0].value_num, 4)

    def test_quantity_source_fallback(self) -> None:
        """When no `source` field is present, source_name is 'Health Auto Export'."""
        metric = self._qty_metric(
            "Blood Oxygen Saturation",
            "%",
            [{"date": "2024-01-16 02:30:00 -0500", "qty": 0.95}],
        )
        recs = list(parse_hae_metric(metric))
        assert recs[0].source_name == "Health Auto Export"

    def test_sleep_source_overridden_when_present(self) -> None:
        """When `source` is present in a sleep point, it overrides the default."""
        metric: dict[str, object] = {
            "name": "Sleep Analysis",
            "units": "hr",
            "data": [
                {
                    "startDate": "2024-01-16 00:00:00 -0500",
                    "endDate": "2024-01-16 01:00:00 -0500",
                    "value": "Core",
                    "source": "Will's Apple Watch",
                }
            ],
        }
        recs = list(parse_hae_metric(metric))
        assert recs[0].source_name == "Will's Apple Watch"


class TestNoonSplitInHAE:
    """Correct night_date is applied to HAE records."""

    def test_sleep_23_00_start_has_correct_night_date(self) -> None:
        """23:00 sleep start stays on the same calendar date."""
        result = parse_payload(_load_fixture())
        sleep_recs = [r for r in result.records if r.record_type == SLEEP_TYPE]
        # All sleep records in the fixture start at 23:xx Jan 15 or 0x:xx Jan 16
        # → all have night_date 2024-01-15
        for r in sleep_recs:
            assert r.night_date == date(2024, 1, 15), (
                f"Unexpected night_date {r.night_date} for {r.start_time}"
            )


class TestUnknownMetricCounting:
    """Unknown metrics are counted, not raised on."""

    def test_unknown_metric_counted_with_point_count(self) -> None:
        """Fixture's step_count metric contributes 1 point to unknown_metrics."""
        result = parse_payload(_load_fixture())
        # Fixture uses snake_case "step_count" — stored under original spelling.
        assert result.unknown_metrics.get("step_count", 0) == 1

    def test_unknown_metric_stored_under_original_name(self) -> None:
        """Unknown names are stored exactly as they arrived, not normalized."""
        payload: dict[str, object] = {
            "data": {
                "metrics": [
                    {
                        "name": "Step Count",  # display-string form of an unknown metric
                        "units": "count",
                        "data": [{"date": "2024-01-16 08:00:00 -0500", "qty": 100}],
                    }
                ]
            }
        }
        result = parse_payload(payload)
        # Must appear under "Step Count", not "step_count".
        assert "Step Count" in result.unknown_metrics
        assert "step_count" not in result.unknown_metrics

    def test_unknown_metric_points_not_in_records(self) -> None:
        """Points from unknown metrics do not appear in result.records."""
        result = parse_payload(_load_fixture())
        hk_ids = {r.record_type for r in result.records}
        assert "HKQuantityTypeIdentifierStepCount" not in hk_ids


class TestMalformedPointHandling:
    """Malformed points within known metrics are skipped, not raised on."""

    def test_missing_date_field_is_skipped(self) -> None:
        """A point missing `date` (quantity) is counted in skipped_points."""
        payload: dict[str, object] = {
            "data": {
                "metrics": [
                    {
                        "name": "Blood Oxygen Saturation",
                        "units": "%",
                        "data": [
                            {"date": "2024-01-16 02:30:00 -0500", "qty": 0.95},
                            {"qty": 0.90},  # missing date
                        ],
                    }
                ]
            }
        }
        result = parse_payload(payload)
        assert len(result.records) == 1
        assert result.skipped_points == 1

    def test_missing_start_end_date_in_sleep_is_skipped(self) -> None:
        """A sleep point missing startDate / endDate is counted in skipped_points."""
        payload: dict[str, object] = {
            "data": {
                "metrics": [
                    {
                        "name": "Sleep Analysis",
                        "units": "hr",
                        "data": [
                            {
                                "startDate": "2024-01-16 00:00:00 -0500",
                                "endDate": "2024-01-16 01:00:00 -0500",
                                "value": "Core",
                            },
                            {},  # malformed
                        ],
                    }
                ]
            }
        }
        result = parse_payload(payload)
        assert len(result.records) == 1
        assert result.skipped_points == 1

    def test_fixture_skipped_empty_dict_counted(self) -> None:
        """The {} point in the fixture sleep metric is counted in skipped_points."""
        result = parse_payload(_load_fixture())
        # Fixture has 7 sleep points (6 valid + 1 empty dict)
        assert result.skipped_points >= 1

    def test_no_exception_on_bad_points(self) -> None:
        """parse_hae_metric never raises even on completely malformed data."""
        metric: dict[str, object] = {
            "name": "Blood Oxygen Saturation",
            "units": "%",
            "data": [None, "bad", 42, {}, {"date": "not-a-date", "qty": 0.9}],
        }
        # Should complete without exception
        recs = list(parse_hae_metric(metric))
        assert isinstance(recs, list)
