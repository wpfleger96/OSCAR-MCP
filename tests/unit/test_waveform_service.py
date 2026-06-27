"""Unit tests for WaveformService."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from snore.database.models import Session, Waveform
from snore.exceptions import NotFoundError
from snore.services.waveform_service import WaveformService


def _make_waveform_blob(sample_count: int, sample_rate: float) -> bytes:
    """Create minimal valid waveform blob for testing."""
    timestamps = np.arange(sample_count, dtype=np.float32) / sample_rate
    values = np.sin(timestamps * 2 * np.pi * 0.2).astype(np.float32)
    data = np.column_stack([timestamps, values])
    return data.tobytes()


class TestWaveformService:
    """Tests for WaveformService."""

    def test_list_waveforms_empty(self, db_session, test_device):
        """Empty session returns empty list."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_empty",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        service = WaveformService(db_session)
        result = service.list_waveforms(session.id)

        assert result == []

    def test_list_waveforms_with_data(self, db_session, test_device):
        """List returns correct WaveformInfo objects."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_with_data",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        wf1 = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=25.0,
            unit="L/min",
            sample_count=1000,
            data_blob=_make_waveform_blob(1000, 25.0),
        )
        wf2 = Waveform(
            session_id=session.id,
            waveform_type="pressure",
            sample_rate=25.0,
            unit="cmH2O",
            sample_count=500,
            data_blob=_make_waveform_blob(500, 25.0),
        )
        db_session.add(wf1)
        db_session.add(wf2)
        db_session.commit()

        service = WaveformService(db_session)
        result = service.list_waveforms(session.id)

        assert len(result) == 2
        assert result[0].waveform_type == "flow"
        assert result[0].sample_rate == 25.0
        assert result[0].sample_count == 1000
        assert result[0].unit == "L/min"
        assert result[1].waveform_type == "pressure"

    def test_list_waveforms_duration_calculation(self, db_session, test_device):
        """Duration hours computed correctly from sample_count/sample_rate."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_duration",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=25.0,
            unit="L/min",
            sample_count=90000,
            data_blob=_make_waveform_blob(90000, 25.0),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)
        result = service.list_waveforms(session.id)

        assert len(result) == 1
        expected_hours = 90000 / 25.0 / 3600
        assert result[0].duration_hours == pytest.approx(expected_hours, rel=1e-6)

    def test_get_waveform_data_not_found(self, db_session, test_device):
        """Invalid session raises ValueError."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_not_found",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        service = WaveformService(db_session)

        with pytest.raises(ValueError, match="Waveform not found"):
            service.get_waveform_data(session.id, "nonexistent")

    def test_get_waveform_data_success(self, db_session, test_device):
        """Load waveform returns correct arrays."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_load",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        sample_count = 1000
        sample_rate = 25.0
        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=sample_rate,
            unit="L/min",
            sample_count=sample_count,
            data_blob=_make_waveform_blob(sample_count, sample_rate),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)
        timestamps, values, metadata = service.get_waveform_data(session.id, "flow")

        assert len(timestamps) == sample_count
        assert len(values) == sample_count
        assert isinstance(timestamps, np.ndarray)
        assert isinstance(values, np.ndarray)
        assert metadata["sample_rate"] == sample_rate
        assert metadata["waveform_type"] == "flow"

    def test_get_waveform_data_with_downsampling(self, db_session, test_device):
        """Downsampling reduces array to max_points."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_downsample",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        sample_count = 10000
        sample_rate = 25.0
        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=sample_rate,
            unit="L/min",
            sample_count=sample_count,
            data_blob=_make_waveform_blob(sample_count, sample_rate),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)
        max_points = 1000
        timestamps, values, metadata = service.get_waveform_data(
            session.id, "flow", max_points=max_points
        )

        assert len(timestamps) == max_points
        assert len(values) == max_points
        assert isinstance(timestamps, np.ndarray)
        assert isinstance(values, np.ndarray)

    def test_get_waveform_data_with_windowing(self, db_session, test_device):
        """Windowing filters data to specified time range."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_window",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        sample_count = 1000
        sample_rate = 25.0
        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=sample_rate,
            unit="L/min",
            sample_count=sample_count,
            data_blob=_make_waveform_blob(sample_count, sample_rate),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)

        timestamps, values, _ = service.get_waveform_data(
            session.id, "flow", start_seconds=10.0, end_seconds=20.0
        )

        assert len(timestamps) > 0
        assert len(timestamps) < sample_count
        assert timestamps.min() >= 10.0
        assert timestamps.max() <= 20.0
        assert len(timestamps) == len(values)

    def test_get_waveform_data_with_start_only(self, db_session, test_device):
        """Windowing with only start_seconds filters correctly."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_window_start",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        sample_count = 1000
        sample_rate = 25.0
        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=sample_rate,
            unit="L/min",
            sample_count=sample_count,
            data_blob=_make_waveform_blob(sample_count, sample_rate),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)

        timestamps, values, _ = service.get_waveform_data(
            session.id, "flow", start_seconds=30.0
        )

        assert len(timestamps) > 0
        assert len(timestamps) < sample_count
        assert timestamps.min() >= 30.0
        assert len(timestamps) == len(values)

    def test_get_waveform_data_with_end_only(self, db_session, test_device):
        """Windowing with only end_seconds filters correctly."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_window_end",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        sample_count = 1000
        sample_rate = 25.0
        wf = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=sample_rate,
            unit="L/min",
            sample_count=sample_count,
            data_blob=_make_waveform_blob(sample_count, sample_rate),
        )
        db_session.add(wf)
        db_session.commit()

        service = WaveformService(db_session)

        timestamps, values, _ = service.get_waveform_data(
            session.id, "flow", end_seconds=10.0
        )

        assert len(timestamps) > 0
        assert len(timestamps) < sample_count
        assert timestamps.max() <= 10.0
        assert len(timestamps) == len(values)


def _make_event(
    start_time: float,
    duration: float = 10.0,
    event_type: str = "OA",
    confidence: float | None = None,
    flow_reduction: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        start_time=start_time,
        duration=duration,
        event_type=event_type,
        confidence=confidence,
        flow_reduction=flow_reduction,
    )


def _make_machine_event(
    start_time: float, duration: float = 10.0, event_type: str = "OA"
) -> SimpleNamespace:
    return SimpleNamespace(
        start_time=start_time,
        duration=duration,
        event_type=event_type,
    )


def _make_analysis_result(
    machine_events: list[SimpleNamespace] | None = None,
    apneas: list[SimpleNamespace] | None = None,
    hypopneas: list[SimpleNamespace] | None = None,
    mode: str = "aasm",
) -> SimpleNamespace:
    mode_result = SimpleNamespace(
        apneas=apneas or [],
        hypopneas=hypopneas or [],
    )
    return SimpleNamespace(
        machine_events=machine_events or [],
        mode_results={mode: mode_result},
    )


class TestCompareEvents:
    def test_no_analysis_result_raises_not_found(
        self, db_session, test_device, monkeypatch
    ):
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_none",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: None,
        )
        service = WaveformService(db_session)
        with pytest.raises(NotFoundError, match="No analysis results"):
            service.compare_events(session.id)

    def test_missing_mode_raises_not_found(self, db_session, test_device, monkeypatch):
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_mode",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        fake_result = _make_analysis_result(mode="aasm")
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: fake_result,
        )
        service = WaveformService(db_session)
        with pytest.raises(NotFoundError, match="Mode.*not found"):
            service.compare_events(session.id, mode="resmed")

    def test_no_events_returns_empty(self, db_session, test_device, monkeypatch):
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_empty",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        fake_result = _make_analysis_result()
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: fake_result,
        )
        monkeypatch.setattr(
            "snore.analysis.utils.convert_machine_events",
            lambda events: ([], []),
        )

        service = WaveformService(db_session)
        result = service.compare_events(session.id)
        assert result.false_negatives == []
        assert result.false_positives_apnea == []
        assert result.false_positives_hypopnea == []

    def test_false_negative_detected(self, db_session, test_device, monkeypatch):
        """Machine event with no matching programmatic event → false negative."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_fn",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        m_event = _make_machine_event(start_time=100.0, event_type="OA")
        fake_result = _make_analysis_result(machine_events=[m_event])
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: fake_result,
        )
        monkeypatch.setattr(
            "snore.analysis.utils.convert_machine_events",
            lambda events: ([m_event], []),
        )

        service = WaveformService(db_session)
        result = service.compare_events(session.id)
        assert len(result.false_negatives) == 1
        assert result.false_negatives[0].start_time == 100.0

    def test_false_positive_detected(self, db_session, test_device, monkeypatch):
        """Programmatic event with no matching machine event → false positive."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_fp",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        prog_event = _make_event(start_time=200.0, event_type="OA")
        fake_result = _make_analysis_result(apneas=[prog_event])
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: fake_result,
        )
        monkeypatch.setattr(
            "snore.analysis.utils.convert_machine_events",
            lambda events: ([], []),
        )

        service = WaveformService(db_session)
        result = service.compare_events(session.id)
        assert len(result.false_positives_apnea) == 1
        assert result.false_positives_apnea[0].start_time == 200.0

    def test_matching_events_excluded(self, db_session, test_device, monkeypatch):
        """Events at same time should not appear in false positive/negative lists."""
        now = datetime(2025, 1, 1, 0, 0, 0)
        session = Session(
            device_id=test_device.id,
            device_session_id="test_compare_match",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.flush()

        m_event = _make_machine_event(start_time=100.0)
        prog_event = _make_event(start_time=100.0, event_type="OA")
        fake_result = _make_analysis_result(
            machine_events=[m_event], apneas=[prog_event]
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.get_analysis_result",
            lambda self, sid: fake_result,
        )
        monkeypatch.setattr(
            "snore.analysis.utils.convert_machine_events",
            lambda events: ([m_event], []),
        )

        service = WaveformService(db_session)
        result = service.compare_events(session.id)
        assert result.false_negatives == []
        assert result.false_positives_apnea == []
