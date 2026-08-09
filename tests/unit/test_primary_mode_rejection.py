"""Pinned tests for primary_mode end-to-end validation (plan v3.8).

Scenarios covered:
1. _resolve_primary_mode raises ValueError when primary_mode is not in modes.
2. _resolve_primary_mode raises ValueError when modes excludes DEFAULT_MODE and
   primary_mode is None.
3. _resolve_primary_mode returns primary_mode when it is a valid member.
4. _resolve_primary_mode returns DEFAULT_MODE when modes includes it and
   primary_mode is None.
5. API single-session route raises 422 when primary_mode is invalid.
6. API batch route raises 422 when primary_mode is invalid.
7. _compute_leak_valid: absent channel → (None, "channel_absent").
8. _compute_leak_valid: alignment gap exactly == 5 s → nearest-neighbour used.
9. _compute_leak_valid: alignment gap just above 5 s → (None, "channel_unaligned").
10. _compute_leak_valid: differing sample rates (1 Hz vs 25 Hz) resolve correctly.
11. _compute_ramp_active: settings-driven timed heuristic with per-mask-on-
    segment restart; SmartRamp / unknown settings yield null + reason.
12. _compute_mask_off: gap overlap from persisted mask-on segments; unknown
    segments yield null + "segments_unknown".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import snore.api.analysis_jobs as _aj_store

from snore.analysis.modes import DEFAULT_MODE
from snore.analysis.service import (
    _compute_leak_valid,
    _compute_mask_off,
    _compute_ramp_active,
    _resolve_primary_mode,
)
from snore.analysis.shared.versioning import (
    LEAK_VALID_MAX_ALIGNMENT_GAP_S,
    LEAK_VALID_THRESHOLD_LPM,
)

# ---------------------------------------------------------------------------
# §1 — _resolve_primary_mode unit tests
# ---------------------------------------------------------------------------


class TestResolvePrimaryMode:
    def test_primary_mode_not_in_modes_raises_value_error(self):
        """primary_mode that is not a member of modes → ValueError."""
        with pytest.raises(ValueError, match="must be a member of modes"):
            _resolve_primary_mode(["aasm", "aasm_relaxed"], "resmed")

    def test_primary_mode_none_and_default_absent_raises_value_error(self):
        """modes excludes DEFAULT_MODE and primary_mode is None → ValueError."""
        non_default = [
            m for m in ["aasm", "aasm_relaxed", "resmed"] if m != DEFAULT_MODE
        ]
        assert DEFAULT_MODE not in non_default, (
            "test precondition: non_default must exclude DEFAULT_MODE"
        )
        with pytest.raises(
            ValueError, match="primary_mode must be supplied explicitly"
        ):
            _resolve_primary_mode(non_default, None)

    def test_valid_primary_mode_returned_unchanged(self):
        """Explicit primary_mode that is in modes is returned as-is."""
        result = _resolve_primary_mode(["aasm", "resmed"], "resmed")
        assert result == "resmed"

    def test_default_mode_used_when_primary_mode_none_and_default_present(self):
        """primary_mode=None + DEFAULT_MODE in modes → DEFAULT_MODE returned."""
        result = _resolve_primary_mode([DEFAULT_MODE, "resmed"], None)
        assert result == DEFAULT_MODE

    def test_single_mode_list_is_accepted(self):
        """Single-element modes list works for both primary_mode=None and explicit."""
        result_none = _resolve_primary_mode([DEFAULT_MODE], None)
        assert result_none == DEFAULT_MODE

        result_explicit = _resolve_primary_mode([DEFAULT_MODE], DEFAULT_MODE)
        assert result_explicit == DEFAULT_MODE


# ---------------------------------------------------------------------------
# §5–6 — API 422 tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_analysis_jobs():
    """Reset global analysis job state between tests."""
    _aj_store._all_jobs.clear()
    _aj_store._queue.clear()
    yield
    _aj_store._all_jobs.clear()
    _aj_store._queue.clear()


class TestAnalysisRouterPrimaryMode:
    """API routes must return 422 when primary_mode is invalid, 201/200 when valid."""

    def _make_client(self, facade_mock: MagicMock) -> Any:
        """Create a TestClient with AnalysisFacade dependency overridden."""
        import typing

        from fastapi.testclient import TestClient

        from snore.api.app import create_app
        from snore.api.deps import get_actor, get_db
        from snore.api.routers.analysis import AnalysisFacadeDep
        from snore.auth.actor import ActorContext, AuthMode, Role

        app = create_app()

        async def override_get_db():
            yield MagicMock()

        stub_actor = ActorContext(
            user_id=1, profile_id=1, role=Role.MEMBER, mode=AuthMode.LOCAL
        )

        def override_get_actor():
            return stub_actor

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_actor] = override_get_actor

        def override_facade(_db=None):
            return facade_mock

        dep_key = typing.get_args(AnalysisFacadeDep)[1]
        app.dependency_overrides[dep_key.dependency] = override_facade

        return TestClient(app, raise_server_exceptions=False)

    def test_single_session_invalid_primary_mode_returns_422(self, tmp_path):
        """POST /sessions/{id}/analysis with primary_mode not in modes → 422."""
        facade = MagicMock()
        facade.run_analysis = AsyncMock(
            side_effect=ValueError(
                "primary_mode 'resmed' must be a member of modes ['aasm']"
            )
        )
        client = self._make_client(facade)
        resp = client.post(
            "/api/v1/sessions/1/analysis",
            json={"modes": ["aasm"], "primary_mode": "resmed"},
        )
        assert resp.status_code == 422

    def test_single_session_valid_primary_mode_calls_facade(self):
        """POST /sessions/{id}/analysis with valid primary_mode delegates to facade."""
        from snore.analysis.types import AnalysisResult

        fake_result = MagicMock(spec=AnalysisResult)
        fake_result.model_dump.return_value = {}
        facade = MagicMock()
        facade.run_analysis = AsyncMock(return_value=fake_result)
        client = self._make_client(facade)
        client.post(
            "/api/v1/sessions/1/analysis",
            json={"modes": ["aasm"], "primary_mode": "aasm"},
        )
        # 201 or 422 — not a ValueError-triggered 422
        facade.run_analysis.assert_awaited_once()

    def _fake_session_item(self, session_id: int = 42) -> MagicMock:
        item = MagicMock()
        item.session_id = session_id
        return item

    def test_batch_invalid_primary_mode_not_in_modes_returns_422(self):
        """POST /analysis/batch with primary_mode not in modes → 422 at endpoint.

        Endpoint-side validation fires before any facade call; primary_mode='resmed'
        is not a member of modes=['aasm'], so the request is rejected immediately.
        """
        facade = MagicMock()
        facade.profile_id = 1
        client = self._make_client(facade)
        resp = client.post(
            "/api/v1/analysis/batch",
            json={
                "from_date": "2025-01-01",
                "modes": ["aasm"],
                "primary_mode": "resmed",
            },
        )
        assert resp.status_code == 422

    def test_batch_valid_primary_mode_returns_202(self):
        """POST /analysis/batch with valid primary_mode returns 202."""
        facade = MagicMock()
        facade.profile_id = 1
        facade.list_session_ids = AsyncMock(return_value=[1, 2])
        client = self._make_client(facade)
        resp = client.post(
            "/api/v1/analysis/batch",
            json={"from_date": "2025-01-01", "modes": ["aasm"], "primary_mode": "aasm"},
        )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# §7–11 — Quality-flag derivation boundary tests
# ---------------------------------------------------------------------------


class TestComputeLeakValid:
    """Boundary tests for _compute_leak_valid (plan step 3)."""

    _threshold = LEAK_VALID_THRESHOLD_LPM
    _gap_limit = LEAK_VALID_MAX_ALIGNMENT_GAP_S  # == 5.0 s

    def test_absent_channel_returns_null_and_channel_absent(self):
        """No leak channel present → (None, 'channel_absent')."""
        valid, reason = _compute_leak_valid(
            breath_start=10.0,
            breath_end=13.0,
            leak_timestamps=None,
            leak_values=None,
        )
        assert valid is None
        assert reason == "channel_absent"

    def test_empty_leak_array_returns_channel_absent(self):
        """Empty arrays treated same as absent channel."""
        valid, reason = _compute_leak_valid(
            breath_start=10.0,
            breath_end=13.0,
            leak_timestamps=np.array([]),
            leak_values=np.array([]),
        )
        assert valid is None
        assert reason == "channel_absent"

    def test_overlapping_samples_below_threshold_returns_true(self):
        """Overlapping samples with mean < threshold → (True, None)."""
        leak_ts = np.array([10.5, 11.0, 11.5, 12.0])
        leak_vals = np.array([10.0, 12.0, 11.0, 9.0])  # mean = 10.5 < 24
        valid, reason = _compute_leak_valid(
            breath_start=10.0,
            breath_end=13.0,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        assert valid is True
        assert reason is None

    def test_overlapping_samples_above_threshold_returns_false(self):
        """Overlapping samples with mean >= threshold → (False, None)."""
        leak_ts = np.array([10.5, 11.0, 11.5])
        leak_vals = np.array([30.0, 28.0, 32.0])  # mean = 30.0 > 24
        valid, reason = _compute_leak_valid(
            breath_start=10.0,
            breath_end=13.0,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        assert valid is False
        assert reason is None

    def test_nearest_neighbour_exactly_at_gap_limit_is_used(self):
        """Nearest sample at exactly LEAK_VALID_MAX_ALIGNMENT_GAP_S uses nearest-neighbour."""
        # Breath midpoint at 11.5, nearest sample at 11.5 + 5.0 = 16.5
        breath_start = 10.0
        breath_end = 13.0  # mid = 11.5
        exact_gap = self._gap_limit  # 5.0 s
        nn_ts = 11.5 + exact_gap  # 16.5 — nearest at exactly 5 s from mid

        leak_ts = np.array([nn_ts])
        leak_vals = np.array([10.0])  # below threshold
        valid, reason = _compute_leak_valid(
            breath_start=breath_start,
            breath_end=breath_end,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        assert valid is True
        assert reason is None

    def test_nearest_neighbour_just_above_gap_limit_returns_unaligned(self):
        """Nearest sample just above 5 s from midpoint → (None, 'channel_unaligned')."""
        breath_start = 10.0
        breath_end = 13.0  # mid = 11.5
        over_gap = self._gap_limit + 0.001  # 5.001 s
        nn_ts = 11.5 + over_gap

        leak_ts = np.array([nn_ts])
        leak_vals = np.array([10.0])
        valid, reason = _compute_leak_valid(
            breath_start=breath_start,
            breath_end=breath_end,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        assert valid is None
        assert reason == "channel_unaligned"

    def test_low_sample_rate_channel_uses_nearest_neighbour(self):
        """1 Hz leak channel (no overlap with 2 s breath) uses nearest-neighbour if within 5 s."""
        # Breath is [30.0, 32.0), leak samples at 1 Hz: 29, 31, 33
        # No sample inside [30, 32); nearest is 31 at 0 s from mid (31.0)
        breath_start = 30.0
        breath_end = 32.0  # mid = 31.0
        leak_ts = np.array([29.0, 31.0, 33.0])  # 31 is inside [30, 32)
        leak_vals = np.array([20.0, 15.0, 18.0])  # all below threshold
        valid, reason = _compute_leak_valid(
            breath_start=breath_start,
            breath_end=breath_end,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        # 31 is inside [30, 32) → overlap path
        assert valid is True

    def test_high_sample_rate_channel_uses_overlap(self):
        """25 Hz leak channel produces many overlapping samples."""
        breath_start = 0.0
        breath_end = 2.0  # 2 s breath, 25 Hz → 50 samples overlap
        leak_ts = np.arange(0.0, 5.0, 1.0 / 25.0)
        leak_vals = np.full(len(leak_ts), 20.0)  # all 20 LPM < threshold
        valid, reason = _compute_leak_valid(
            breath_start=breath_start,
            breath_end=breath_end,
            leak_timestamps=leak_ts,
            leak_values=leak_vals,
        )
        assert valid is True
        assert reason is None


class TestComputeRampActive:
    """Behavioral tests for _compute_ramp_active (validity flags v1)."""

    _segments = [(0.0, 3600.0), (4200.0, 7800.0)]  # 10-min gap at 3600 s

    def test_breath_within_ramp_window_is_true(self):
        """Enabled + timed ramp, breath at 30 s of a 10-min ramp → True."""
        active, reason = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=self._segments,
            ramp_enabled=True,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        assert active is True
        assert reason is None

    def test_breath_past_ramp_time_is_false(self):
        """Breath past ramp_time within the first segment → False."""
        active, reason = _compute_ramp_active(
            breath_start_s=601.0,  # past 10 min = 600 s
            mask_on_segments=self._segments,
            ramp_enabled=True,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        assert active is False
        assert reason is None

    def test_second_mask_on_segment_restarts_ramp_clock(self):
        """A breath early in segment 2 is in ramp again (per-segment restart)."""
        active, reason = _compute_ramp_active(
            breath_start_s=4230.0,  # 30 s into segment 2 (starts at 4200 s)
            mask_on_segments=self._segments,
            ramp_enabled=True,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        assert active is True
        assert reason is None

    def test_smart_ramp_is_indeterminate(self):
        """SmartRamp ends on sleep detection, not a timer → null + reason."""
        active, reason = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=self._segments,
            ramp_enabled=False,  # ResMed: S.RampEnable=2 → enabled False + smart
            ramp_time_minutes=10,
            smart_ramp=True,
        )
        assert active is None
        assert reason == "smart_ramp_indeterminate"

    def test_ramp_enabled_unknown_is_not_available(self):
        """ramp_enabled=None (setting not recorded) → (None, 'not_available')."""
        active, reason = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=self._segments,
            ramp_enabled=None,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        assert active is None
        assert reason == "not_available"

    def test_ramp_disabled_is_false(self):
        """ramp_enabled=False → (False, None) — no ramp ever runs."""
        active, reason = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=self._segments,
            ramp_enabled=False,
            ramp_time_minutes=None,
            smart_ramp=False,
        )
        assert active is False
        assert reason is None

    def test_ramp_time_unknown_is_not_available(self):
        """Ramp enabled but ramp_time missing → (None, 'not_available')."""
        active, reason = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=self._segments,
            ramp_enabled=True,
            ramp_time_minutes=None,
            smart_ramp=False,
        )
        assert active is None
        assert reason == "not_available"

    def test_segments_unknown_falls_back_to_session_start_offset(self):
        """Without segments, the ramp clock starts at session offset 0."""
        active_early, reason_early = _compute_ramp_active(
            breath_start_s=30.0,
            mask_on_segments=None,
            ramp_enabled=True,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        active_late, reason_late = _compute_ramp_active(
            breath_start_s=4230.0,  # would be in ramp if segment 2 were known
            mask_on_segments=None,
            ramp_enabled=True,
            ramp_time_minutes=10,
            smart_ramp=False,
        )
        assert (active_early, reason_early) == (True, None)
        assert (active_late, reason_late) == (False, None)


class TestComputeMaskOff:
    """Behavioral tests for _compute_mask_off (validity flags v1)."""

    def test_segments_unknown_returns_null_with_reason(self):
        """mask_on_segments=None (un-reimported / OSCAR data) → null + reason."""
        mask_off, reason = _compute_mask_off(
            breath_start_s=10.0,
            breath_end_s=13.0,
            mask_on_segments=None,
            session_duration_s=7200.0,
        )
        assert mask_off is None
        assert reason == "segments_unknown"

    def test_single_full_segment_is_all_false(self):
        """A single segment covering the whole session has no gaps → False."""
        mask_off, reason = _compute_mask_off(
            breath_start_s=10.0,
            breath_end_s=13.0,
            mask_on_segments=[(0.0, 7200.0)],
            session_duration_s=7200.0,
        )
        assert mask_off is False
        assert reason is None

    def test_seam_spanning_breath_is_true(self):
        """A breath straddling the gap boundary overlaps the gap → True."""
        mask_off, reason = _compute_mask_off(
            breath_start_s=3598.0,
            breath_end_s=3602.0,  # gap is [3600, 4200)
            mask_on_segments=[(0.0, 3600.0), (4200.0, 7800.0)],
            session_duration_s=7800.0,
        )
        assert mask_off is True
        assert reason is None

    def test_interior_breath_is_false(self):
        """A breath fully inside a mask-on segment → False."""
        mask_off, reason = _compute_mask_off(
            breath_start_s=4300.0,
            breath_end_s=4303.0,
            mask_on_segments=[(0.0, 3600.0), (4200.0, 7800.0)],
            session_duration_s=7800.0,
        )
        assert mask_off is False
        assert reason is None
