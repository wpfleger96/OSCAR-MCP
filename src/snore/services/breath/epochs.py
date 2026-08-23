"""compare_epochs — cross-epoch breath-feature distributions."""

from __future__ import annotations

import math
import statistics

from datetime import date
from typing import Any

from sqlalchemy import select

from snore.analysis.rx_tracker import RX_KEYS, changed_setting_keys
from snore.analysis.shared.versioning import (
    CROSS_VERSION_REFUSAL_KEYS,
    RERA_PROXY_ALGO_VERSION,
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisStatus,
    NullReason,
)
from snore.constants import FlowLimitationConstants as FLC
from snore.database import models
from snore.utils.stats import percentile_nearest_rank

from ._core import _BreathServiceCore
from .algorithms import _count_fl_run_reras
from .dtos import (
    CompareEpochsResult,
    DeviceAmbiguityError,
    DeviceNotOwnedError,
    DistributionMetric,
    DistributionStats,
    EpochBreathStats,
    EpochRequest,
    EpochRxViolation,
)


def _null_epoch_stats(
    *,
    label: str,
    date_start: date,
    date_end: date,
    null_reason: NullReason,
    rera_reason: NullReason,
    nights_with_data: int = 0,
    nights_missing_analysis: int = 0,
    rx_settings: dict[str, str] | None = None,
) -> EpochBreathStats:
    """EpochBreathStats with all distributions nulled (refusal/no-data epochs)."""
    null_dist = DistributionStats(
        median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
    )
    return EpochBreathStats(
        label=label,
        date_start=date_start,
        date_end=date_end,
        nights_with_data=nights_with_data,
        nights_missing_analysis=nights_missing_analysis,
        algorithm_identity=None,
        null_reason=null_reason,
        primary_mode=None,
        mid_insp_flattening=null_dist,
        flatness_index=null_dist,
        flow_class_distribution={},
        flow_class_distribution_fallback={},
        tidal_volume_ml=null_dist,
        ie_ratio=null_dist,
        rera_proxy_count=None,
        rera_reason=rera_reason,
        rx_settings=rx_settings or {},
    )


class EpochsMixin(_BreathServiceCore):
    """Epoch-comparison methods."""

    async def compare_epochs(
        self,
        epochs: list[EpochRequest],
        metrics: list[DistributionMetric] | None = None,
    ) -> CompareEpochsResult:
        """Distributions across RxTracker epochs.

        Two-phase design: metadata checks (RX + identity) run across ALL epochs
        BEFORE any breath queries.

        Per-epoch nulling: a mid-epoch RX change nulls only the epoch it occurred
        in (RX_CHANGED_WITHIN_EPOCH); clean epochs in the same comparison still
        compute normally.  The top-level null_reason is RX_CHANGED_WITHIN_EPOCH
        only when EVERY epoch was nulled for an RX change; otherwise None.
        rx_violations is fully populated either way.
        Warning (non-blocking): CROSS_VERSION_REFUSAL_KEYS differ across epochs —
        distributions are still computed; callers should inspect version_warnings.
        Mixed primary modes degrade RERA fields only (PRIMARY_MODE_MISMATCH).

        flow_class_distribution counts rule-matched classifications only (the same
        confidence gate as nightly fl_class_ge4_pct); low-confidence fallback
        guesses are reported separately in flow_class_distribution_fallback.
        """
        from snore.services.breath_service import BreathService  # noqa: PLC0415

        # Validate date order for each epoch before anything else
        for epoch in epochs:
            if epoch.date_start > epoch.date_end:
                raise ValueError(
                    f"Epoch '{epoch.label}': date_start ({epoch.date_start})"
                    f" must be <= date_end ({epoch.date_end})"
                )

        _null_dist = DistributionStats(
            median=None, iqr=None, p95=None, n_breaths=0, n_nights=0
        )

        # -----------------------------------------------------------------------
        # Phase 1: Resolve ONE device for the whole comparison, then per-epoch sessions.
        # All epochs must target the same device.  Union-resolve fires DeviceAmbiguityError
        # if multiple owned devices span the combined date range and no device_id is given.
        # -----------------------------------------------------------------------

        explicit_device_ids = {e.device_id for e in epochs if e.device_id is not None}
        if len(explicit_device_ids) > 1:
            raise ValueError(
                "All epochs in a comparison must target the same device_id"
            )
        union_device_id = explicit_device_ids.pop() if explicit_device_ids else None
        union_start = min(e.date_start for e in epochs)
        union_end = max(e.date_end for e in epochs)
        # Union-resolve to guarantee one device across all epochs.
        # DeviceAmbiguityError (multi-device profile, no device_id) propagates.
        # ValueError for a foreign/unknown explicit device_id: return NOT_AVAILABLE for all epochs.
        try:
            union_resolved_device_id, _ = await self._resolve_range(
                union_start, union_end, union_device_id
            )
        except DeviceAmbiguityError:
            raise
        except DeviceNotOwnedError:
            # Explicit foreign device → NOT_AVAILABLE for all epochs
            not_avail_epochs = [
                _null_epoch_stats(
                    label=e.label,
                    date_start=e.date_start,
                    date_end=e.date_end,
                    null_reason=NullReason.NOT_AVAILABLE,
                    rera_reason=NullReason.NOT_AVAILABLE,
                )
                for e in epochs
            ]
            return CompareEpochsResult(
                null_reason=NullReason.NOT_AVAILABLE,
                epochs=not_avail_epochs,
                version_warnings=[],
            )
        except ValueError:
            # ValueError here means auto-select found no sessions in range — always
            # NO_DATA_IN_RANGE (the NOT_AVAILABLE arm required union_device_id is not None,
            # but _resolve_range raises DeviceNotOwnedError for foreign explicit devices).
            no_data_reason = NullReason.NO_DATA_IN_RANGE
            null_epochs = [
                _null_epoch_stats(
                    label=e.label,
                    date_start=e.date_start,
                    date_end=e.date_end,
                    null_reason=no_data_reason,
                    rera_reason=NullReason.NOT_AVAILABLE,
                )
                for e in epochs
            ]
            return CompareEpochsResult(
                null_reason=no_data_reason,
                epochs=null_epochs,
                version_warnings=[],
            )

        # Each entry: dict with epoch metadata + resolved sessions + RX data
        epoch_resolved: list[dict[str, Any]] = []
        rx_violations: list[EpochRxViolation] = []

        for epoch in epochs:
            try:
                resolved_device_id, sessions_by_date = await self._resolve_range(
                    epoch.date_start, epoch.date_end, union_resolved_device_id
                )
            except ValueError:
                # Foreign/unknown device_id → NOT_AVAILABLE; no sessions → NO_DATA_IN_RANGE
                no_data_reason = (
                    NullReason.NO_DATA_IN_RANGE
                    if epoch.device_id is None
                    else NullReason.NOT_AVAILABLE
                )
                epoch_resolved.append(
                    {
                        "epoch": epoch,
                        "null_reason": no_data_reason,
                        "contributing_sessions": [],
                        "all_rx": [],
                        "nights_with_data": 0,
                        "nights_missing_analysis": 0,
                        "rx_violation": None,
                        "ar_ids": {},
                    }
                )
                continue
            # DeviceAmbiguityError propagates to caller

            # Collect per-session RX snapshots and contributing sessions (OK only)
            contributing_sessions: list[tuple[int, AlgoVersions]] = []
            # Each entry: (therapy_date, rx_dict) for change-date tracking
            session_rx_dated: list[tuple[date, dict[str, str]]] = []
            nights_with_data = 0
            nights_missing_analysis = 0
            ar_ids_for_epoch: dict[int, int | None] = {}

            for therapy_date, sessions in sessions_by_date.items():
                ok_on_date: list[tuple[int, AlgoVersions]] = []
                for sess in sessions:
                    status, algo, ar_id = await self._latest_analysis_for_session(
                        sess.id
                    )
                    if status == AnalysisStatus.OK and algo is not None:
                        ok_on_date.append((sess.id, algo))
                        ar_ids_for_epoch[sess.id] = ar_id
                        # Per-session RX snapshot (not merged per-day)
                        setting_rows = (
                            (
                                await self._db.execute(
                                    select(models.Setting).where(
                                        models.Setting.session_id == sess.id,
                                        models.Setting.key.in_(RX_KEYS),
                                        models.Setting.value.is_not(None),
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        rx_snap = {s.key: s.value for s in setting_rows if s.value}
                        session_rx_dated.append((therapy_date, rx_snap))

                if ok_on_date:
                    nights_with_data += 1
                    contributing_sessions.extend(ok_on_date)
                else:
                    nights_missing_analysis += 1

            # Within-epoch RX homogeneity check (per-session, not per-day merged)
            rx_violation: EpochRxViolation | None = None
            if len(session_rx_dated) > 1:
                first_rx_snap = session_rx_dated[0][1]
                if any(rx != first_rx_snap for _, rx in session_rx_dated[1:]):
                    change_dates: list[date] = []
                    changed_keys: set[str] = set()
                    prev_rx = session_rx_dated[0][1]
                    for snap_date, snap_rx in session_rx_dated[1:]:
                        diffs = changed_setting_keys(prev_rx, snap_rx)
                        if diffs:
                            changed_keys |= diffs
                            change_dates.append(snap_date)
                        prev_rx = snap_rx
                    rx_violation = EpochRxViolation(
                        epoch_label=epoch.label,
                        changed_keys=sorted(changed_keys),
                        change_dates=change_dates,
                    )
                    rx_violations.append(rx_violation)

            # An epoch's own mid-epoch RX change nulls only that epoch; clean
            # epochs in the same comparison still compute normally (per-epoch
            # nulling, not a batch refusal).
            if rx_violation is not None:
                epoch_null_reason: NullReason | None = (
                    NullReason.RX_CHANGED_WITHIN_EPOCH
                )
            elif contributing_sessions:
                epoch_null_reason = None
            else:
                epoch_null_reason = NullReason.NO_DATA_IN_RANGE

            epoch_resolved.append(
                {
                    "epoch": epoch,
                    "null_reason": epoch_null_reason,
                    "contributing_sessions": contributing_sessions,
                    "all_rx": [rx for _, rx in session_rx_dated],
                    "nights_with_data": nights_with_data,
                    "nights_missing_analysis": nights_missing_analysis,
                    "rx_violation": rx_violation,
                    "ar_ids": ar_ids_for_epoch,
                }
            )

        # -----------------------------------------------------------------------
        # Phase 2: Cross-epoch identity check (BEFORE any breath queries)
        # -----------------------------------------------------------------------

        # Gather all identities from ALL contributing sessions across ALL epochs
        all_identities_combined: list[AlgorithmIdentity] = [
            algo.identity
            for ed in epoch_resolved
            for _, algo in ed["contributing_sessions"]
        ]

        # Collect version warnings for CROSS_VERSION_REFUSAL_KEYS fields that differ
        # across contributing sessions.  This is non-blocking: distributions are still
        # computed; callers should inspect version_warnings for compatibility context.
        version_warnings: list[str] = []
        if len(all_identities_combined) > 1:
            cross_keys = CROSS_VERSION_REFUSAL_KEYS
            all_dumps = [id_.model_dump() for id_ in all_identities_combined]
            for k in sorted(cross_keys):
                all_vals_for_key = sorted({d[k] for d in all_dumps})
                if len(all_vals_for_key) > 1:
                    vals_str = ", ".join(f"'{v}'" for v in all_vals_for_key)
                    version_warnings.append(
                        f"algorithm_identity.{k} differs across epochs: {vals_str}"
                    )

        # RX change within an epoch nulls that epoch only (its distributions are
        # meaningless once therapy settings changed mid-range); clean epochs still
        # compute below.  The top-level null_reason reflects a whole-comparison
        # refusal only when EVERY epoch was nulled for the RX change.
        all_epochs_rx_changed = all(
            ed["null_reason"] == NullReason.RX_CHANGED_WITHIN_EPOCH
            for ed in epoch_resolved
        )
        top_null_reason = (
            NullReason.RX_CHANGED_WITHIN_EPOCH if all_epochs_rx_changed else None
        )

        # -----------------------------------------------------------------------
        # Phase 3: Compute distributions (only if all checks passed)
        # -----------------------------------------------------------------------

        def _distrib(
            vals: list[float], n_breaths: int, n_nights: int
        ) -> DistributionStats:
            if not vals:
                return DistributionStats(
                    median=None,
                    iqr=None,
                    p95=None,
                    n_breaths=n_breaths,
                    n_nights=n_nights,
                )
            sorted_v = sorted(vals)
            p25 = sorted_v[len(sorted_v) // 4]
            p75 = sorted_v[min(len(sorted_v) * 3 // 4, len(sorted_v) - 1)]
            p95 = percentile_nearest_rank(sorted_v, 0.95)
            return DistributionStats(
                median=statistics.median(vals),
                iqr=p75 - p25,
                p95=p95,
                n_breaths=n_breaths,
                n_nights=n_nights,
            )

        requested = set(metrics) if metrics is not None else set(DistributionMetric)
        epoch_stats: list[EpochBreathStats] = []

        # Bulk-fetch waveform channel values for all contributing sessions across
        # all epochs in one query, then slice per epoch below.
        all_contrib_session_ids: list[int] = [
            sid for ed in epoch_resolved for sid, _ in ed["contributing_sessions"]
        ]
        (
            all_fl_by_sess,
            all_snore_by_sess,
        ) = await BreathService._fetch_waveform_channel_vals(
            self._db, all_contrib_session_ids
        )

        for ed in epoch_resolved:
            epoch = ed["epoch"]
            contributing_sessions = ed["contributing_sessions"]
            nights_with_data = ed["nights_with_data"]
            nights_missing_analysis = ed["nights_missing_analysis"]
            all_rx = ed["all_rx"]
            null_reason_ed: NullReason | None = ed["null_reason"]

            if null_reason_ed is not None or not contributing_sessions:
                resolved_null_reason = null_reason_ed or NullReason.NO_DATA_IN_RANGE
                # rera_reason must mirror the actual per-epoch null cause: an epoch
                # nulled for a mid-epoch RX change carries that reason, not the
                # generic ANALYSIS_NOT_RUN used for the no-data path.
                epoch_rera_reason = (
                    NullReason.RX_CHANGED_WITHIN_EPOCH
                    if resolved_null_reason == NullReason.RX_CHANGED_WITHIN_EPOCH
                    else NullReason.ANALYSIS_NOT_RUN
                )
                epoch_stats.append(
                    _null_epoch_stats(
                        label=epoch.label,
                        date_start=epoch.date_start,
                        date_end=epoch.date_end,
                        nights_with_data=nights_with_data,
                        nights_missing_analysis=nights_missing_analysis,
                        null_reason=resolved_null_reason,
                        rera_reason=epoch_rera_reason,
                        rx_settings=all_rx[0] if all_rx else {},
                    )
                )
                continue

            all_identities = [algo.identity for _, algo in contributing_sessions]

            # Check primary_mode uniformity for RERA
            all_modes_str = [algo.run.primary_mode for _, algo in contributing_sessions]
            if len(set(all_modes_str)) == 1:
                uniform_primary_mode: str | None = all_modes_str[0]
                rera_reason: NullReason | None = None
            else:
                uniform_primary_mode = None
                rera_reason = NullReason.PRIMARY_MODE_MISMATCH

            ar_ids = ed["ar_ids"]
            contributing_ar_ids: list[tuple[int, int]] = [
                (sid, ar_ids[sid])
                for sid, _ in contributing_sessions
                if ar_ids.get(sid) is not None
            ]

            all_breath_rows: list[Any] = []
            for _sid, ar_id in contributing_ar_ids:
                brows = (
                    (
                        await self._db.execute(
                            select(models.Breath).where(
                                models.Breath.analysis_result_id == ar_id,
                                models.Breath.leak_valid.is_(True),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                all_breath_rows.extend(brows)

            n_lv = len(all_breath_rows)
            n_nights_contrib = nights_with_data

            mif_vals = [
                b.mid_insp_flattening
                for b in all_breath_rows
                if b.mid_insp_flattening is not None
            ]
            fi_vals = [
                b.flatness_index
                for b in all_breath_rows
                if b.flatness_index is not None
            ]
            tv_vals = [
                b.tidal_volume_ml
                for b in all_breath_rows
                if b.tidal_volume_ml is not None
            ]
            ie_vals = [b.i_e_ratio for b in all_breath_rows if b.i_e_ratio is not None]
            # Split FL classifications by the same confidence gate nightly uses:
            # rule-matched (flow_confidence > FL_DEFAULT_CONFIDENCE) go in fc_dist so
            # the class>=4 fraction reconciles with nightly fl_class_ge4_pct; low-
            # confidence fallback flatness-triage guesses (confidence exactly at the
            # default) go in fc_dist_fallback so they don't inflate FL rates.  Every
            # breath with a non-null flow_class lands in exactly one dict.
            fc_dist: dict[int, int] = {}
            fc_dist_fallback: dict[int, int] = {}
            for b in all_breath_rows:
                if b.flow_class is None:
                    continue
                if (
                    b.flow_confidence is not None
                    and b.flow_confidence > FLC.FL_DEFAULT_CONFIDENCE
                ):
                    fc_dist[b.flow_class] = fc_dist.get(b.flow_class, 0) + 1
                else:
                    fc_dist_fallback[b.flow_class] = (
                        fc_dist_fallback.get(b.flow_class, 0) + 1
                    )

            # RERA proxy: FL runs ending in recovery breath
            rera_count: int | None = None
            if uniform_primary_mode is not None:
                rera_count = 0
                for _sid, ar_id in contributing_ar_ids:
                    brows_all = (
                        (
                            await self._db.execute(
                                select(models.Breath)
                                .where(models.Breath.analysis_result_id == ar_id)
                                .order_by(models.Breath.breath_number)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    rera_count += _count_fl_run_reras(brows_all)

            # Device waveform channel distributions for this epoch.
            # Contributing session IDs (OK analysis only; same sessions used for
            # breath-level distributions, ensuring apples-to-apples comparison).
            # Waveform data was bulk-fetched before the loop; slice to this epoch's sessions.
            epoch_fl_by_sess = all_fl_by_sess
            epoch_snore_by_sess = all_snore_by_sess

            epoch_fl_all: list[float] = []
            epoch_snore_all: list[float] = []
            epoch_fl_nights = 0
            epoch_snore_nights = 0
            # Group by session for per-night counting — each unique date with at
            # least one sample counts as one night.
            fl_nights_set: set[int] = set()
            snore_nights_set: set[int] = set()
            for sid, _ in contributing_sessions:
                if sid in epoch_fl_by_sess:
                    # Filter negative sentinel values and non-finite values.
                    valid_fl = [
                        v for v in epoch_fl_by_sess[sid] if v >= 0 and math.isfinite(v)
                    ]
                    epoch_fl_all.extend(valid_fl)
                    if valid_fl:
                        fl_nights_set.add(sid)
                if sid in epoch_snore_by_sess:
                    valid_sn = [v for v in epoch_snore_by_sess[sid] if math.isfinite(v)]
                    epoch_snore_all.extend(valid_sn)
                    if valid_sn:
                        snore_nights_set.add(sid)
            # Use unique session count as night proxy (good enough for cross-epoch compare).
            # Note: this is a session-proxy count, not a deduplicated calendar-date count —
            # a multi-session night contributes once per contributing session.
            epoch_fl_nights = len(fl_nights_set)
            epoch_snore_nights = len(snore_nights_set)

            epoch_stats.append(
                EpochBreathStats(
                    label=epoch.label,
                    date_start=epoch.date_start,
                    date_end=epoch.date_end,
                    nights_with_data=nights_with_data,
                    nights_missing_analysis=nights_missing_analysis,
                    algorithm_identity=all_identities[0],
                    null_reason=None,
                    primary_mode=uniform_primary_mode,
                    mid_insp_flattening=_distrib(mif_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.MID_INSP_FLATTENING in requested
                    else _null_dist,
                    flatness_index=_distrib(fi_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.FLATNESS_INDEX in requested
                    else _null_dist,
                    flow_class_distribution=fc_dist,
                    flow_class_distribution_fallback=fc_dist_fallback,
                    tidal_volume_ml=_distrib(tv_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.TIDAL_VOLUME_ML in requested
                    else _null_dist,
                    ie_ratio=_distrib(ie_vals, n_lv, n_nights_contrib)
                    if DistributionMetric.IE_RATIO in requested
                    else _null_dist,
                    rera_proxy_count=rera_count,
                    rera_reason=rera_reason,
                    rera_proxy_version=(
                        RERA_PROXY_ALGO_VERSION if rera_count is not None else None
                    ),
                    rx_settings=all_rx[0] if all_rx else {},
                    device_flg=_distrib(
                        epoch_fl_all, len(epoch_fl_all), epoch_fl_nights
                    )
                    if DistributionMetric.DEVICE_FLG in requested
                    else _null_dist,
                    snore_dist=_distrib(
                        epoch_snore_all, len(epoch_snore_all), epoch_snore_nights
                    )
                    if DistributionMetric.SNORE in requested
                    else _null_dist,
                )
            )

        # Future-proofing: warn when rera_proxy_version differs across epochs.
        # Currently impossible (single constant), but guards against future bumps.
        rera_versions = {
            es.rera_proxy_version
            for es in epoch_stats
            if es.rera_proxy_version is not None
        }
        if len(rera_versions) > 1:
            sorted_vers = sorted(rera_versions)
            vals_str = ", ".join(f"'{v}'" for v in sorted_vers)
            version_warnings.append(
                f"rera_proxy_version differs across epochs: {vals_str}"
            )

        return CompareEpochsResult(
            epochs=epoch_stats,
            null_reason=top_null_reason,
            rx_violations=rx_violations,
            version_warnings=version_warnings,
        )
