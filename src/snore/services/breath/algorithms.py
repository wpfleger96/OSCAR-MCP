"""Pure (non-service) algorithm functions for the breath service package."""

from __future__ import annotations

import statistics

from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np

from snore.analysis.data.waveform_loader import deserialize_waveform_blob
from snore.analysis.shared.versioning import DayAnalysisStatus, NullReason
from snore.analysis.types import AnalysisResult as AnalysisResultDTO
from snore.services.lttb import lttb_downsample

from .dtos import (
    CaAnalysisResult,
    CaDetail,
    MvSource,
    RawCaAnalysis,
    RawWaveformWindow,
    WaveformChannel,
    WaveformChannelName,
    WaveformWindow,
)


def _extract_window_mean(
    offsets: list[float],
    values: list[float],
    offset_start: float,
    offset_end: float,
) -> float | None:
    """Mean of values whose offset falls in [offset_start, offset_end]. None if empty."""
    slice_vals = [
        v
        for o, v in zip(offsets, values, strict=True)
        if offset_start <= o <= offset_end
    ]
    return sum(slice_vals) / len(slice_vals) if slice_vals else None


def compute_waveform_window(raw: RawWaveformWindow) -> WaveformWindow:
    """Pure — no DB access. Deserializes bytes, slices window, applies LTTB."""

    request = raw.request
    channels_out: list[WaveformChannel] = []
    missing_channels: list[WaveformChannelName] = list(raw.missing_channels)

    for raw_ch in raw.channels:
        if raw_ch.sample_count <= 0 or not raw_ch.raw_bytes:
            missing_channels.append(raw_ch.waveform_type)
            continue
        try:
            timestamps, values = deserialize_waveform_blob(
                raw_ch.raw_bytes, raw_ch.sample_count
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid waveform data for channel '{raw_ch.waveform_type.value}'"
            ) from exc
        # Slice to requested window
        mask = (timestamps >= request.offset_start) & (timestamps <= request.offset_end)
        ts_slice = timestamps[mask]
        v_slice = values[mask]

        original_count = int(len(ts_slice))
        is_downsampled = False
        if request.max_points is not None and original_count > request.max_points:
            # LTTB downsampling: lttb_downsample(timestamps, values, target_points)
            if len(ts_slice) >= 3:
                ts_ds, v_ds = lttb_downsample(ts_slice, v_slice, request.max_points)
                ts_slice = ts_ds
                v_slice = v_ds
                is_downsampled = True

        channels_out.append(
            WaveformChannel(
                channel_type=raw_ch.waveform_type,
                unit=raw_ch.unit,
                sample_rate=raw_ch.sample_rate,
                offset_seconds=ts_slice.tolist(),
                values=v_slice.tolist(),
                original_sample_count=original_count,
                is_downsampled=is_downsampled,
            )
        )

    missing_reason: NullReason | None = (
        NullReason.CHANNEL_ABSENT if missing_channels else None
    )

    return WaveformWindow(
        session_id=raw.session_id,
        session_start_wall_clock=raw.session_start_wall_clock,
        timezone_status=raw.timezone_status,
        timezone_name=raw.timezone_name,
        window_start_offset=request.offset_start,
        window_end_offset=request.offset_end,
        channels=channels_out,
        missing_channels=missing_channels,
        missing_channel_reason=missing_reason,
    )


# ---------------------------------------------------------------------------
# §13 — BreathService helpers
# ---------------------------------------------------------------------------


def _iter_fl_run_recoveries(
    breath_rows: Sequence[Any],
    *,
    fl_class_threshold: int = 4,
    min_fl_run_length: int = 2,
    recovery_amplitude_margin: float = 0.20,
) -> Iterator[tuple[int, int, int]]:
    """Yield (run_start_idx, run_last_idx, recovery_idx) per RERA-proxy event.

    A qualifying event is a run of >= ``min_fl_run_length`` consecutive breaths
    with ``flow_class >= fl_class_threshold`` whose immediately-next breath
    (no gap; a ``flow_class is None`` breath ends the run) is a recovery
    breath.  The follower is a recovery breath when EITHER:

    (a) ``is_recovery_breath is True`` — the analysis-time amplitude detector; OR
    (b) self-contained (RERA-proxy v2): the follower's ``flow_class`` drops to
        <= 2 AND its ``peak_flow_lpm`` is >= ``(1 + recovery_amplitude_margin)``
        times the mean of the run's non-null ``peak_flow_lpm`` values.

    Missing data (null ``flow_class`` or ``peak_flow_lpm`` on the follower, or
    an all-null-peak run) never satisfies (b).
    """
    n = len(breath_rows)
    i = 0
    while i < n:
        b = breath_rows[i]
        if b.flow_class is None or b.flow_class < fl_class_threshold:
            i += 1
            continue
        run_start = i
        while (
            i < n
            and breath_rows[i].flow_class is not None
            and breath_rows[i].flow_class >= fl_class_threshold
        ):
            i += 1
        if i - run_start < min_fl_run_length or i >= n:
            continue
        follower = breath_rows[i]
        is_recovery = follower.is_recovery_breath is True
        if (
            not is_recovery
            and follower.flow_class is not None
            and follower.flow_class <= 2
            and follower.peak_flow_lpm is not None
        ):
            run_peaks = [
                breath_rows[k].peak_flow_lpm
                for k in range(run_start, i)
                if breath_rows[k].peak_flow_lpm is not None
            ]
            if run_peaks:
                run_mean = sum(run_peaks) / len(run_peaks)
                is_recovery = follower.peak_flow_lpm >= (
                    (1.0 + recovery_amplitude_margin) * run_mean
                )
        if is_recovery:
            yield (run_start, i - 1, i)


def _count_fl_run_reras(
    breath_rows: Sequence[Any],
    fl_class_threshold: int = 4,
    min_fl_run_length: int = 2,
    recovery_amplitude_margin: float = 0.20,
) -> int:
    """Count RERA-proxy events: FL runs ending in a recovery breath."""
    return sum(
        1
        for _ in _iter_fl_run_recoveries(
            breath_rows,
            fl_class_threshold=fl_class_threshold,
            min_fl_run_length=min_fl_run_length,
            recovery_amplitude_margin=recovery_amplitude_margin,
        )
    )


# ---------------------------------------------------------------------------
# §12 — compute_ca_analysis (module-level pure function)
# ---------------------------------------------------------------------------


def derive_mv_from_flow(
    offsets: np.ndarray,
    values: np.ndarray,
    *,
    window_s: float = 60.0,
    out_dt_s: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Pure — derive minute ventilation (L/min) from a flow waveform (L/min).

    MV(t) = mean of positive-clipped flow over the trailing window
    ``[t - window_s, t]``, sampled every ``out_dt_s`` seconds starting at
    ``offsets[0] + window_s`` up to the last input offset.  Output samples
    whose window contains zero input samples are omitted — merged sessions
    have timestamp gaps, so uniform sampling is never assumed.

    Returns ``(out_offsets, out_values)``; empty arrays when the input is too
    short to cover a single window or when ``offsets`` is not non-decreasing
    (searchsorted requires sorted input — unsorted offsets would silently
    produce garbage windows, so downstream metrics go null instead).  NaN
    samples in ``values`` are treated as 0.0 flow so they cannot poison the
    cumulative sum.  O(n log n): cumsum + searchsorted, no per-window scans.
    """
    if offsets.size == 0 or float(offsets[-1]) - float(offsets[0]) < window_s:
        return np.array([]), np.array([])
    if np.any(np.diff(offsets) < 0):
        return np.array([]), np.array([])

    clipped = np.clip(np.where(np.isnan(values), 0.0, values), 0.0, None)
    csum = np.concatenate(([0.0], np.cumsum(clipped, dtype=np.float64)))

    out_times = np.arange(
        float(offsets[0]) + window_s, float(offsets[-1]) + 1e-9, out_dt_s
    )
    # Window [t - window_s, t] inclusive both ends
    lo = np.searchsorted(offsets, out_times - window_s, side="left")
    hi = np.searchsorted(offsets, out_times, side="right")
    counts = hi - lo
    mask = counts > 0
    mv = (csum[hi[mask]] - csum[lo[mask]]) / counts[mask]
    return out_times[mask], mv


def compute_ca_analysis(raw: RawCaAnalysis) -> CaAnalysisResult:
    """Pure — no DB access. Runs numpy/statistics on pre-fetched raw CA data.

    Deserializes waveform blobs via ``compute_waveform_window``, slices
    per-event windows with searchsorted, computes MV slope/stability/PS,
    accumulates cross-session MV bin means, and derives PB% and rolling
    MV variance.

    Empty ``raw.session_data`` (signalling an empty day) is mapped to a
    NOT_RUN ``CaAnalysisResult`` sentinel consistent with ``get_ca_analysis``.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        compute_waveform_window,
    )

    if not raw.session_data:
        return CaAnalysisResult(
            query_date=raw.therapy_date,
            device_id=raw.device_id,
            day_status=raw.day_status,
            session_coverage=[],
            algorithm_identity=raw.algorithm_identity,
            null_reason=raw.null_reason,
            ca_events=[],
            periodic_breathing_pct=None,
            pb_reason=NullReason.NOT_AVAILABLE,
            mv_rolling_variance=None,
            mv_variance_reason=NullReason.NOT_AVAILABLE,
        )

    coverage = [sd.coverage for sd in raw.session_data]
    night_level_refused = raw.day_status == DayAnalysisStatus.MIXED_VERSION

    # Helper: linear regression slope (rise/run), returns L/min per SECOND
    def _mv_slope(xs: list[float], ys: list[float]) -> float | None:
        n = len(xs)
        if n < 2:
            return None
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den != 0.0 else None

    ca_details: list[CaDetail] = []
    total_pb_s = 0.0
    total_eligible_s = 0.0
    # True when PB detection ran for ≥1 OK session (pb_json persisted) — zero
    # episodes on an analyzed night is a real 0.0 %, not "not_available".
    pb_ran_any = False
    # Combined MV bin means from ALL OK sessions for cross-session variance
    combined_bin_means: list[float] = []
    mv_rolling_var: float | None = None
    mv_var_reason: NullReason | None = NullReason.NOT_AVAILABLE
    # MV provenance per contributing session (DEVICE / FLOW_DERIVED)
    mv_sources_seen: set[MvSource] = set()

    for sd in raw.session_data:
        session_start_f = sd.session_start.timestamp()

        # Deserialize pre-fetched waveform blobs → numpy arrays (mirrors get_ca_analysis).
        # compute_waveform_window deserializes once per channel; converting to ndarray
        # here enables O(log n) per-event slicing via searchsorted.
        pre_window = compute_waveform_window(sd.pre_waveform)
        pre_ch: dict[WaveformChannelName, tuple[np.ndarray, np.ndarray]] = {
            ch.channel_type: (
                np.array(ch.offset_seconds),
                np.array(ch.values),
            )
            for ch in pre_window.channels
        }

        # MV fallback: no device MV channel → derive MV from the flow waveform
        # and insert it under the MV key so all downstream code (slope,
        # stability, rolling variance) works unchanged.
        mv_source: MvSource | None = None
        if WaveformChannelName.MV in pre_ch:
            mv_source = MvSource.DEVICE
        elif sd.flow_waveform is not None:
            # Deserialize the raw FLOW blob straight to numpy — bypassing the
            # render-oriented compute_waveform_window avoids a numpy → list →
            # numpy round trip over the full-session flow signal.  Window
            # slicing and corrupt-blob semantics mirror compute_waveform_window.
            flow_req = sd.flow_waveform.request
            for flow_ch in sd.flow_waveform.channels:
                if flow_ch.waveform_type != WaveformChannelName.FLOW:
                    continue
                if flow_ch.sample_count <= 0 or not flow_ch.raw_bytes:
                    break  # absent channel → no fallback (mv_source stays None)
                try:
                    flow_off, flow_val = deserialize_waveform_blob(
                        flow_ch.raw_bytes, flow_ch.sample_count
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid waveform data for channel "
                        f"'{flow_ch.waveform_type.value}'"
                    ) from exc
                in_window = (flow_off >= flow_req.offset_start) & (
                    flow_off <= flow_req.offset_end
                )
                mv_off, mv_val = derive_mv_from_flow(
                    flow_off[in_window], flow_val[in_window]
                )
                if mv_off.size > 0:
                    pre_ch[WaveformChannelName.MV] = (mv_off, mv_val)
                    mv_source = MvSource.FLOW_DERIVED
                break
        if mv_source is not None:
            mv_sources_seen.add(mv_source)

        for raw_ev in sd.ca_events:
            ev_start_f = raw_ev.start_time.timestamp()
            offset_s = ev_start_f - session_start_f

            # --- preceding_mv_slope + stability_index ---
            # Contract: both metrics use the 60 s window preceding the event
            preceding_mv_slope: float | None = None
            preceding_mv_reason: NullReason | None = NullReason.NOT_AVAILABLE
            stability_index: float | None = None
            stability_reason: NullReason | None = NullReason.NOT_AVAILABLE

            if offset_s > 0.0 and WaveformChannelName.MV in pre_ch:
                mv_win_start = max(0.0, offset_s - 60.0)
                off_mv, val_mv = pre_ch[WaveformChannelName.MV]
                # searchsorted: O(log n) per event, inclusive both ends
                lo = int(np.searchsorted(off_mv, mv_win_start, side="left"))
                hi = int(np.searchsorted(off_mv, offset_s, side="right"))
                ts_slice = off_mv[lo:hi]
                v_slice = val_mv[lo:hi]
                if len(ts_slice) >= 2:
                    # Contract: slope is reported in L/min per MINUTE;
                    # _mv_slope returns L/min per SECOND (offset_seconds as x)
                    slope_per_s = _mv_slope(ts_slice.tolist(), v_slice.tolist())
                    if slope_per_s is not None:
                        # convert: multiply by 60 s/min → L/min per minute
                        preceding_mv_slope = slope_per_s * 60.0
                    preceding_mv_reason = (
                        None
                        if preceding_mv_slope is not None
                        else NullReason.NOT_AVAILABLE
                    )
                    if len(ts_slice) >= 3:
                        mean_mv = float(v_slice.mean())
                        if mean_mv != 0.0:
                            stability_index = (
                                statistics.stdev(v_slice.tolist()) / mean_mv
                            )
                            stability_reason = None

            # --- ps_delivered_cmh2o: mean(THERAPY_PRESSURE - EPAP) over ±5 s ---
            ps_delivered: float | None = None
            ps_reason: NullReason | None = NullReason.NOT_AVAILABLE

            ps_win_start = max(0.0, offset_s - 5.0)
            ps_win_end = offset_s + 5.0
            if ps_win_end > 0.0:
                if (
                    WaveformChannelName.THERAPY_PRESSURE in pre_ch
                    and WaveformChannelName.EPAP in pre_ch
                ):
                    off_tp, val_tp = pre_ch[WaveformChannelName.THERAPY_PRESSURE]
                    off_ep, val_ep = pre_ch[WaveformChannelName.EPAP]
                    # searchsorted: O(log n) per event, inclusive both ends
                    tp_lo = int(np.searchsorted(off_tp, ps_win_start, side="left"))
                    tp_hi = int(np.searchsorted(off_tp, ps_win_end, side="right"))
                    ep_lo = int(np.searchsorted(off_ep, ps_win_start, side="left"))
                    ep_hi = int(np.searchsorted(off_ep, ps_win_end, side="right"))
                    tp_slice = val_tp[tp_lo:tp_hi]
                    ep_slice = val_ep[ep_lo:ep_hi]
                    if len(tp_slice) > 0 and len(ep_slice) > 0:
                        min_len = min(len(tp_slice), len(ep_slice))
                        ps_delivered = float(
                            np.mean(tp_slice[:min_len] - ep_slice[:min_len])
                        )
                        ps_reason = None

            ca_details.append(
                CaDetail(
                    session_id=sd.session_id,
                    session_start_wall_clock=sd.session_start,
                    timezone_status=raw.timezone_status,
                    timezone_name=raw.timezone_name,
                    offset_seconds=offset_s,
                    duration_seconds=raw_ev.duration_seconds,
                    preceding_mv_slope=preceding_mv_slope,
                    preceding_mv_reason=preceding_mv_reason,
                    ps_delivered_cmh2o=ps_delivered,
                    ps_reason=ps_reason,
                    stability_index=stability_index,
                    stability_reason=stability_reason,
                    mv_source=mv_source,
                )
            )

        # Night-level metrics: OK sessions ONLY (eligibility gate)
        if sd.is_ok and not night_level_refused:
            total_eligible_s += sd.duration_seconds

            # PB% from persisted AnalysisResult JSON
            if sd.pb_json is not None:
                pb_ran_any = True
                dto = AnalysisResultDTO.model_validate(sd.pb_json)
                for ep in dto.periodic_breathing_episodes or []:
                    start_t = float(ep.get("start_time", ep.get("start", 0)))
                    end_t = float(
                        ep.get(
                            "end_time",
                            ep.get("end", start_t + ep.get("duration", 0)),
                        )
                    )
                    total_pb_s += max(0.0, end_t - start_t)

            # MV rolling variance: collect bin means across ALL OK sessions
            # (combined; variance computed once after the loop).
            # Vectorized with numpy: one searchsorted pass per bin rather than
            # a full-list comprehension, and max() hoisted out of the loop.
            if WaveformChannelName.MV in pre_ch:
                ts_arr, v_arr = pre_ch[WaveformChannelName.MV]
                if ts_arr.size >= 6:
                    max_t = float(ts_arr.max())
                    bin_size = 600.0
                    for bin_start in np.arange(0.0, max_t, bin_size):
                        bin_end = float(bin_start) + bin_size
                        lo = int(np.searchsorted(ts_arr, bin_start, side="left"))
                        hi = int(np.searchsorted(ts_arr, bin_end, side="left"))
                        if lo < hi:
                            combined_bin_means.append(float(v_arr[lo:hi].mean()))

    # Compute cross-session MV variance from combined bin means (OK sessions only)
    if not night_level_refused and len(combined_bin_means) >= 2:
        mv_rolling_var = statistics.variance(combined_bin_means)
        mv_var_reason = None

    # Compute pb_pct over eligible (OK) sessions only
    pb_pct: float | None = None
    pb_reason: NullReason | None = NullReason.NOT_AVAILABLE
    if night_level_refused:
        pb_reason = NullReason.ALGO_VERSION_MISMATCH
        mv_var_reason = NullReason.ALGO_VERSION_MISMATCH
    elif pb_ran_any and total_eligible_s > 0:
        # PB detection ran → zero episodes is a genuine 0.0 %, not null.
        # total_eligible_s == 0 (NULL session durations) stays null+NOT_AVAILABLE.
        pb_pct = total_pb_s / total_eligible_s * 100.0
        pb_reason = None

    # Aggregate MV provenance across contributing sessions
    if not mv_sources_seen:
        night_mv_source: MvSource | None = None
    elif len(mv_sources_seen) == 1:
        night_mv_source = next(iter(mv_sources_seen))
    else:
        night_mv_source = MvSource.MIXED

    return CaAnalysisResult(
        query_date=raw.therapy_date,
        device_id=raw.device_id,
        day_status=raw.day_status,
        session_coverage=coverage,
        algorithm_identity=raw.algorithm_identity,
        null_reason=raw.null_reason,
        ca_events=ca_details,
        periodic_breathing_pct=pb_pct,
        pb_reason=pb_reason,
        mv_rolling_variance=mv_rolling_var,
        mv_variance_reason=mv_var_reason,
        mv_source=night_mv_source,
    )
