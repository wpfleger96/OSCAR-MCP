"""Unit tests for parallelized multi-type waveform loading in the show command."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from click.testing import CliRunner

from snore.cli.groups.waveform import waveform


@asynccontextmanager
async def _mock_session_scope():
    """Yield a mock DB session.  session_id pass-through requires no real DB."""
    yield MagicMock()


class TestWaveformMultiTypeAllFail:
    """All waveform types return empty data: command should fail with a clear message."""

    def test_waveform_multi_all_fail_raises_click_exception(self):
        runner = CliRunner()

        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            # session_scope is imported inside show_waveform; patch at source
            patch(
                "snore.database.session.session_scope", side_effect=_mock_session_scope
            ),
            # WaveformInspector is imported from snore.waveform inside show_waveform
            patch("snore.waveform.WaveformInspector") as mock_inspector_cls,
            patch("snore.waveform.WaveformRenderer"),
        ):
            mock_inspector = mock_inspector_cls.return_value
            mock_inspector.get_window = AsyncMock(
                return_value=(np.array([]), np.array([]), {})
            )

            result = runner.invoke(
                waveform,
                [
                    "show",
                    "--session-id",
                    "1",
                    "--time",
                    "01:00:00",
                    "--type",
                    "flow,pressure",
                ],
            )

        assert result.exit_code != 0
        assert "No waveform data loaded" in result.output


class TestWaveformMultiTypePartialFailure:
    """One type succeeds, one raises: warning is emitted and render proceeds with good data."""

    def test_waveform_multi_partial_failure_continues(self):
        runner = CliRunner()

        good_timestamps = np.array([1.0, 2.0, 3.0])
        good_values = np.array([0.1, 0.2, 0.3])

        async def get_window_side_effect(**kwargs):
            if kwargs.get("waveform_type") == "flow":
                return (good_timestamps, good_values, {"waveform_type": "flow"})
            raise RuntimeError("pressure sensor unavailable")

        mock_renderer = MagicMock()

        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch(
                "snore.database.session.session_scope", side_effect=_mock_session_scope
            ),
            patch("snore.waveform.WaveformInspector") as mock_inspector_cls,
            patch("snore.waveform.WaveformRenderer", return_value=mock_renderer),
        ):
            mock_inspector = mock_inspector_cls.return_value
            mock_inspector.get_window = AsyncMock(side_effect=get_window_side_effect)

            result = runner.invoke(
                waveform,
                [
                    "show",
                    "--session-id",
                    "1",
                    "--time",
                    "01:00:00",
                    "--type",
                    "flow,pressure",
                ],
            )

        # Command should succeed because at least one type loaded data
        assert result.exit_code == 0, f"Unexpected exit:\n{result.output}"

        # Warning about the failed type is printed
        assert "pressure" in result.output

        # render_multi called once with only the flow data tuple
        mock_renderer.render_multi.assert_called_once()
        call_kwargs = mock_renderer.render_multi.call_args
        passed_data = call_kwargs.kwargs.get(
            "waveform_data",
            call_kwargs.args[0] if call_kwargs.args else [],
        )
        types_in_call = [entry[2] for entry in passed_data]
        assert types_in_call == ["flow"]


class TestWaveformMultiTypeOrdering:
    """Data passed to render_multi must match the user-specified --type order."""

    def test_waveform_multi_ordering_preserved(self):
        runner = CliRunner()

        ts = np.array([1.0, 2.0])
        vals = np.array([0.5, 0.6])

        async def get_window_side_effect(**kwargs):
            wf_type = kwargs.get("waveform_type", "")
            return (ts, vals, {"waveform_type": wf_type})

        mock_renderer = MagicMock()

        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch(
                "snore.database.session.session_scope", side_effect=_mock_session_scope
            ),
            patch("snore.waveform.WaveformInspector") as mock_inspector_cls,
            patch("snore.waveform.WaveformRenderer", return_value=mock_renderer),
        ):
            mock_inspector = mock_inspector_cls.return_value
            mock_inspector.get_window = AsyncMock(side_effect=get_window_side_effect)

            result = runner.invoke(
                waveform,
                [
                    "show",
                    "--session-id",
                    "1",
                    "--time",
                    "01:00:00",
                    "--type",
                    "flow,pressure,leak",
                ],
            )

        assert result.exit_code == 0, f"Unexpected exit:\n{result.output}"

        mock_renderer.render_multi.assert_called_once()
        call_kwargs = mock_renderer.render_multi.call_args
        passed_data = call_kwargs.kwargs.get(
            "waveform_data",
            call_kwargs.args[0] if call_kwargs.args else [],
        )

        # Must preserve the user's declared order, not the arbitrary as_completed order
        types_in_call = [entry[2] for entry in passed_data]
        assert types_in_call == ["flow", "pressure", "leak"]
