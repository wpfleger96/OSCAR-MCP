"""Unit tests for MCP Pydantic schemas and schema map."""

from __future__ import annotations

from snore.mcp.schemas import (
    SCHEMA_MODEL_MAP,
    ComplianceFields,
    DeviceInfo,
    NightlyRow,
    model_to_schema,
)


class TestSchemaModelMap:
    def test_all_expected_keys_present(self) -> None:
        expected = {
            "device_capabilities",
            "device_info",
            "data_overview",
            "settings_epoch",
            "settings_timeline",
            "nightly_row",
            "compliance_fields",
            "nightly_summary",
            "event_context",
            "event_row",
            "events_response",
            "capability_entry",
        }
        assert expected <= set(SCHEMA_MODEL_MAP.keys())

    def test_model_to_schema_returns_dict_with_properties(self) -> None:
        schema = model_to_schema(DeviceInfo)
        assert "properties" in schema
        assert "id" in schema["properties"]

    def test_all_models_produce_valid_json_schema(self) -> None:
        for name, model in SCHEMA_MODEL_MAP.items():
            schema = model_to_schema(model)
            assert "type" in schema or "properties" in schema or "$defs" in schema, (
                f"Schema for {name!r} lacks type/properties/$defs"
            )


class TestNightlyRowNullReasons:
    def test_rera_index_nullable_with_reason(self) -> None:
        from datetime import date

        row = NightlyRow(
            date=date(2024, 1, 1),
            rera_index=None,
            rera_index_reason="analysis_not_run",
        )
        assert row.rera_index is None
        assert row.rera_index_reason == "analysis_not_run"

    def test_rdi_nullable_with_reason(self) -> None:
        from datetime import date

        row = NightlyRow(
            date=date(2024, 1, 1),
            rdi=None,
            rdi_reason="analysis_not_run",
        )
        assert row.rdi is None

    def test_all_optional_fields_default_to_none(self) -> None:
        from datetime import date

        row = NightlyRow(date=date(2024, 1, 1))
        assert row.ahi is None
        assert row.rera_index is None
        assert row.leak_median_lpm is None
        assert row.spo2_mean_pct is None


class TestComplianceFields:
    def test_compliance_fields_round_trip(self) -> None:
        cf = ComplianceFields(
            threshold_hours=4.0,
            days_compliant=25,
            days_total=30,
            compliance_pct=83.3,
        )
        d = cf.model_dump()
        assert d["days_compliant"] == 25
        assert d["compliance_pct"] == 83.3

    def test_compliance_fields_serializes_to_json(self) -> None:
        import json

        cf = ComplianceFields(
            threshold_hours=4.0,
            days_compliant=25,
            days_total=30,
            compliance_pct=83.3,
        )
        payload = json.loads(cf.model_dump_json())
        assert payload["threshold_hours"] == 4.0
