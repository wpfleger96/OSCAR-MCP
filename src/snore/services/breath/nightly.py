"""Nightly summaries — per-night and range aggregation."""

from __future__ import annotations

import math
import statistics

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from snore.analysis.queries import latest_analysis_row
from snore.analysis.shared.versioning import (
    RERA_PROXY_ALGO_VERSION,
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisStatus,
    NullReason,
)
from snore.database import models
from snore.utils.stats import percentile_nearest_rank

from ._core import _BreathServiceCore
from .algorithms import _count_fl_run_reras
from .dtos import (
    DeviceAmbiguityError,
    DeviceNotOwnedError,
    NightlyAnalysisSummary,
    NightlyRangeSummary,
    NoSessionsInRangeError,
    SessionCoverage,
)


def _stat_pair(
    value: float | None, reason: NullReason
) -> tuple[float | None, NullReason | None]:
    """``(value, None)`` when the value was computable, else ``(None, reason)``."""
    return (value, None) if value is not None else (None, reason)


def _sorted_distribution(
    vals: list[float], reason: NullReason
) -> tuple[float | None, float | None, float | None, NullReason | None]:
    """``(median, p95, max, None)`` over ``vals``, or all-``None`` + reason when empty."""
    if not vals:
        return None, None, None, reason
    sorted_v = sorted(vals)
    return (
        float(statistics.median(sorted_v)),
        percentile_nearest_rank(sorted_v, 0.95),
        sorted_v[-1],
        None,
    )


class NightlyMixin(_BreathServiceCore):
    """Nightly-summary methods."""

    async def get_analysis_status(
        self,
        session_id: int,
    ) -> tuple[AnalysisStatus, AlgoVersions | None]:
        """(status, versions) for a session's latest AnalysisResult.

        Returns (NOT_RUN, None) if the session is not owned by this profile.
        """
        # Verify profile ownership before querying analysis result
        owned = (
            await self._db.execute(
                select(models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    models.Session.id == session_id,
                    models.Device.profile_id == self._profile_id,
                )
            )
        ).scalar_one_or_none()
        if owned is None:
            return AnalysisStatus.NOT_RUN, None

        row = await latest_analysis_row(self._db, session_id)
        if row is None:
            return AnalysisStatus.NOT_RUN, None
        return self._classify_analysis_row(row)

    @staticmethod
    def _build_nightly_summary(
        *,
        therapy_date: date,
        device_id: int,
        day_sessions: list[Any],
        day_row: Any | None,
        ar_classification: dict[
            int, tuple[AnalysisStatus, AlgoVersions | None, int | None]
        ],
        breath_rows_by_ar_id: dict[int, list[Any]],
        compliance_threshold_hours: float,
        fl_vals_by_session: dict[int, list[float]] | None = None,
        snore_vals_by_session: dict[int, list[float]] | None = None,
    ) -> NightlyAnalysisSummary:
        """Build a NightlyAnalysisSummary from pre-fetched data. No I/O."""
        from snore.services.breath_service import BreathService  # noqa: PLC0415

        if day_row is not None and day_row.total_therapy_hours is not None:
            total_therapy_hours = float(day_row.total_therapy_hours)
        else:
            total_therapy_hours = (
                sum(s.duration_seconds or 0.0 for s in day_sessions) / 3600.0
            )

        session_coverages: list[SessionCoverage] = []
        ok_sessions: list[tuple[int, AlgoVersions]] = []
        identities_for_reduce: list[AlgorithmIdentity] = []
        missing_or_stale: list[int] = []
        algo_identity: AlgorithmIdentity | None = None

        for s in day_sessions:
            sid = s.id
            status, algo, _ar_id = ar_classification.get(
                sid, (AnalysisStatus.NOT_RUN, None, None)
            )
            session_coverages.append(
                SessionCoverage(
                    session_id=sid, analysis_status=status, algo_versions=algo
                )
            )
            if status == AnalysisStatus.OK and algo is not None:
                ok_sessions.append((sid, algo))
                identities_for_reduce.append(algo.identity)
                algo_identity = algo.identity
            else:
                missing_or_stale.append(sid)

        eligible = len(day_sessions)
        analyzed = len(ok_sessions)

        day_status = BreathService._reduce_day_status(
            session_coverages, identities_for_reduce
        )
        day_ahi = day_row.ahi if day_row is not None else None

        # Device waveform aggregates — independent of analysis, aggregated over all
        # sessions of the night (not just OK sessions).
        _fl_by_sess = fl_vals_by_session or {}
        _sn_by_sess = snore_vals_by_session or {}

        fl_all: list[float] = []
        snore_all: list[float] = []
        for s in day_sessions:
            # Filter negative sentinel values (−0.01 from digital −1 at mask-off) and
            # non-finite values before aggregating.  Zeros are legitimate — retain them.
            fl_all.extend(
                v for v in _fl_by_sess.get(int(s.id), []) if v >= 0 and math.isfinite(v)
            )
            snore_all.extend(
                v for v in _sn_by_sess.get(int(s.id), []) if math.isfinite(v)
            )

        device_flg_median, device_flg_95th, device_flg_max, device_flg_reason = (
            _sorted_distribution(fl_all, NullReason.CHANNEL_ABSENT)
        )

        snore_median, snore_95th, _, snore_reason = _sorted_distribution(
            snore_all, NullReason.CHANNEL_ABSENT
        )
        snore_pct_time = (
            sum(1 for v in snore_all if v > 0.5) / len(snore_all) if snore_all else None
        )

        if not ok_sessions:
            return NightlyAnalysisSummary(
                therapy_date=therapy_date,
                device_id=device_id,
                day_status=day_status,
                session_coverage=session_coverages,
                eligible_session_count=eligible,
                analyzed_session_count=0,
                missing_or_stale_session_ids=missing_or_stale,
                algorithm_identity=None,
                rera_count=None,
                rera_reason=NullReason.NOT_AVAILABLE,
                primary_mode=None,
                fl_median=None,
                fl_95th=None,
                fl_max=None,
                fl_reason=NullReason.NOT_AVAILABLE,
                fl_class_ge4_pct=None,
                fl_class_ge4_pct_reason=NullReason.NOT_AVAILABLE,
                ti_median_s=None,
                ti_median_reason=NullReason.NOT_AVAILABLE,
                ie_ratio_median=None,
                ie_ratio_reason=NullReason.NOT_AVAILABLE,
                total_therapy_hours=total_therapy_hours,
                compliance_threshold_hours=compliance_threshold_hours,
                is_compliant=total_therapy_hours >= compliance_threshold_hours,
                rera_index=None,
                rera_index_reason=NullReason.NOT_AVAILABLE,
                rdi=None,
                rdi_reason=NullReason.NOT_AVAILABLE,
                leak_above_24_pct=None,
                leak_above_24_pct_reason=NullReason.NOT_AVAILABLE,
                device_flg_median=device_flg_median,
                device_flg_95th=device_flg_95th,
                device_flg_max=device_flg_max,
                device_flg_reason=device_flg_reason,
                snore_median=snore_median,
                snore_95th=snore_95th,
                snore_pct_time=snore_pct_time,
                snore_reason=snore_reason,
            )

        # MIXED_VERSION within a day is handled by _reduce_day_status; under current
        # _latest_analysis_for_session semantics all OK sessions share the current identity.

        modes_seen = {algo.run.primary_mode for _, algo in ok_sessions}
        uniform_primary_mode = next(iter(modes_seen)) if len(modes_seen) == 1 else None

        fl_vals: list[float] = []
        ti_vals: list[float] = []
        ie_vals: list[float] = []
        fl_class_ge4_num = 0
        fl_class_den = 0
        rera_count = 0
        leak_above_24_num = 0  # breaths with leak_valid is False (leak > 24 L/min)
        leak_above_24_den = 0  # breaths with determinate leak_valid (True or False)

        for sid, _algo in ok_sessions:
            _status, _a, ar_id = ar_classification.get(sid, (None, None, None))
            if ar_id is None:
                continue
            breath_rows = breath_rows_by_ar_id.get(ar_id, [])
            for b in breath_rows:
                if b.leak_valid is not None:
                    leak_above_24_den += 1
                    if b.leak_valid is False:
                        leak_above_24_num += 1
                if b.leak_valid is True:
                    if b.mid_insp_flattening is not None:
                        fl_vals.append(b.mid_insp_flattening)
                    if b.inspiration_time_s is not None:
                        ti_vals.append(b.inspiration_time_s)
                    if b.i_e_ratio is not None:
                        ie_vals.append(b.i_e_ratio)
                    if b.flow_class is not None:
                        fl_class_den += 1
                        if b.flow_class >= 4:
                            fl_class_ge4_num += 1
            # RERA proxy: FL runs ending in recovery breath.  Intentionally
            # scans ALL breaths (not just leak-valid) — runs need sequence
            # contiguity.
            rera_count += _count_fl_run_reras(breath_rows)

        fl_median, fl_95th, fl_max, fl_reason = _sorted_distribution(
            fl_vals, NullReason.NOT_AVAILABLE
        )

        fl_class_ge4_pct, fl_class_ge4_pct_reason = _stat_pair(
            100.0 * fl_class_ge4_num / fl_class_den if fl_class_den > 0 else None,
            NullReason.NOT_AVAILABLE,
        )

        ti_median_s, ti_median_reason = _stat_pair(
            float(statistics.median(ti_vals)) if ti_vals else None,
            NullReason.NOT_AVAILABLE,
        )

        ie_ratio_median, ie_ratio_reason = _stat_pair(
            float(statistics.median(ie_vals)) if ie_vals else None,
            NullReason.NOT_AVAILABLE,
        )

        leak_above_24_pct, leak_above_24_pct_reason = _stat_pair(
            round(100.0 * leak_above_24_num / leak_above_24_den, 1)
            if leak_above_24_den > 0
            else None,
            NullReason.NOT_AVAILABLE,
        )

        # rera_index / rdi arithmetic (finding 7 — moves into service)
        rera_reason: NullReason | None = (
            None if uniform_primary_mode is not None else NullReason.NOT_AVAILABLE
        )
        final_rera_count = rera_count if uniform_primary_mode is not None else None

        rera_index: float | None
        rera_index_reason: NullReason | None
        rdi: float | None
        rdi_reason: NullReason | None

        if final_rera_count is not None:
            if total_therapy_hours > 0:
                rera_index = round(final_rera_count / total_therapy_hours, 2)
                rera_index_reason = None
            else:
                rera_index = None
                rera_index_reason = NullReason.DURATION_ZERO
        elif rera_reason is not None:
            rera_index = None
            rera_index_reason = rera_reason
        else:
            rera_index = None
            rera_index_reason = None

        if day_ahi is not None and rera_index is not None:
            rdi = round(day_ahi + rera_index, 2)
            rdi_reason = None
        elif rera_index is None and rera_index_reason is not None:
            rdi = None
            rdi_reason = rera_index_reason
        else:
            rdi = None
            rdi_reason = NullReason.NOT_AVAILABLE

        return NightlyAnalysisSummary(
            therapy_date=therapy_date,
            device_id=device_id,
            day_status=day_status,
            session_coverage=session_coverages,
            eligible_session_count=eligible,
            analyzed_session_count=analyzed,
            missing_or_stale_session_ids=missing_or_stale,
            algorithm_identity=algo_identity,
            rera_count=final_rera_count,
            rera_reason=rera_reason,
            rera_proxy_version=(
                RERA_PROXY_ALGO_VERSION if final_rera_count is not None else None
            ),
            primary_mode=uniform_primary_mode,
            fl_median=fl_median,
            fl_95th=fl_95th,
            fl_max=fl_max,
            fl_reason=fl_reason,
            fl_class_ge4_pct=fl_class_ge4_pct,
            fl_class_ge4_pct_reason=fl_class_ge4_pct_reason,
            ti_median_s=ti_median_s,
            ti_median_reason=ti_median_reason,
            ie_ratio_median=ie_ratio_median,
            ie_ratio_reason=ie_ratio_reason,
            total_therapy_hours=total_therapy_hours,
            compliance_threshold_hours=compliance_threshold_hours,
            is_compliant=total_therapy_hours >= compliance_threshold_hours,
            rera_index=rera_index,
            rera_index_reason=rera_index_reason,
            rdi=rdi,
            rdi_reason=rdi_reason,
            leak_above_24_pct=leak_above_24_pct,
            leak_above_24_pct_reason=leak_above_24_pct_reason,
            device_flg_median=device_flg_median,
            device_flg_95th=device_flg_95th,
            device_flg_max=device_flg_max,
            device_flg_reason=device_flg_reason,
            snore_median=snore_median,
            snore_95th=snore_95th,
            snore_pct_time=snore_pct_time,
            snore_reason=snore_reason,
        )

    async def get_nightly_summary(
        self,
        therapy_date: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyAnalysisSummary:
        """Latest-run analysis fields aggregated across all OK sessions of a day.

        Thin wrapper over ``get_nightly_range_summary`` for the single-night
        range.  Raises ``NoSessionsInRangeError`` when no owned sessions exist
        and no device_id was given, ``ValueError`` when an explicit owned
        device has no sessions on the date; ``DeviceAmbiguityError`` and
        ``DeviceNotOwnedError`` propagate from device resolution.
        """
        range_summary = await self.get_nightly_range_summary(
            therapy_date,
            therapy_date,
            device_id=device_id,
            compliance_threshold_hours=compliance_threshold_hours,
        )
        if not range_summary.nights:
            if device_id is None:
                raise NoSessionsInRangeError(therapy_date, therapy_date)
            raise ValueError(f"No sessions found for date {therapy_date}")
        return range_summary.nights[0]

    async def get_nightly_range_summary(
        self,
        date_start: date,
        date_end: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyRangeSummary:
        """Per-night summaries + aggregate compliance (bulk-query path)."""
        from snore.services.breath_service import BreathService  # noqa: PLC0415

        if date_end < date_start:
            raise ValueError(
                f"date_end ({date_end}) must be >= date_start ({date_start})"
            )

        n_calendar = (date_end - date_start).days + 1

        # Enforce pagination cap (plan Phase 1: "paginated ~30 nights/call").
        _MAX_NIGHTS = 90
        if n_calendar > _MAX_NIGHTS:
            raise ValueError(
                f"Date range spans {n_calendar} nights; maximum per call is "
                f"{_MAX_NIGHTS}. Use multiple calls to page over longer ranges."
            )

        # Resolve device ONCE across the full range.
        # DeviceAmbiguityError and DeviceNotOwnedError propagate (ownership failures).
        # ValueError (no sessions, device_id=None auto-select found nothing) → empty summary.
        try:
            resolved_device_id, sessions_by_date = await self._resolve_range(
                date_start, date_end, device_id
            )
        except (DeviceAmbiguityError, DeviceNotOwnedError):
            raise
        except ValueError:
            return NightlyRangeSummary(
                date_start=date_start,
                date_end=date_end,
                device_id=device_id or 0,
                compliance_threshold_hours=compliance_threshold_hours,
                n_calendar_nights=n_calendar,
                n_nights=0,
                days_compliant=0,
                compliance_pct=0.0,
                nights=[],
            )

        # Bulk Day query for all dates that have sessions
        all_dates = list(sessions_by_date.keys())
        day_rows = (
            (
                await self._db.execute(
                    select(models.Day).where(
                        models.Day.device_id == resolved_device_id,
                        models.Day.date.in_(all_dates),
                    )
                )
            )
            .scalars()
            .all()
        )
        day_by_date: dict[date, Any] = {d.date: d for d in day_rows}

        # Collect all session IDs across the range
        all_sessions: list[Any] = []
        for sessions in sessions_by_date.values():
            all_sessions.extend(sessions)
        all_session_ids = [s.id for s in all_sessions]

        # Bulk latest-AnalysisResult classification (shared helper, no N+1)
        ar_classification = await self._classify_sessions_bulk(all_session_ids)

        # Bulk Breath query for all OK ar_ids (9 columns only)
        ok_ar_ids = [
            ar_id
            for (_status, _algo, ar_id) in ar_classification.values()
            if _status == AnalysisStatus.OK and ar_id is not None
        ]
        breath_rows_by_ar_id: dict[int, list[Any]] = {ar_id: [] for ar_id in ok_ar_ids}

        if ok_ar_ids:
            breath_cols = (
                models.Breath.analysis_result_id,
                models.Breath.breath_number,
                models.Breath.leak_valid,
                models.Breath.mid_insp_flattening,
                models.Breath.inspiration_time_s,
                models.Breath.i_e_ratio,
                models.Breath.flow_class,
                models.Breath.is_recovery_breath,
                models.Breath.peak_flow_lpm,
            )
            breath_result = await self._db.execute(
                select(*breath_cols)
                .where(models.Breath.analysis_result_id.in_(ok_ar_ids))
                .order_by(
                    models.Breath.analysis_result_id,
                    models.Breath.breath_number,
                )
            )
            for row in breath_result:
                breath_rows_by_ar_id[row.analysis_result_id].append(row)

        # Bulk fetch fl and snore waveform values for all sessions in range
        (
            fl_vals_by_session,
            snore_vals_by_session,
        ) = await BreathService._fetch_waveform_channel_vals(self._db, all_session_ids)

        # Per-night builder loop (nights without sessions are skipped)
        nights: list[NightlyAnalysisSummary] = []
        days_compliant = 0
        current = date_start
        while current <= date_end:
            day_sessions = sessions_by_date.get(current, [])
            if day_sessions:
                summary = self._build_nightly_summary(
                    therapy_date=current,
                    device_id=resolved_device_id,
                    day_sessions=day_sessions,
                    day_row=day_by_date.get(current),
                    ar_classification=ar_classification,
                    breath_rows_by_ar_id=breath_rows_by_ar_id,
                    compliance_threshold_hours=compliance_threshold_hours,
                    fl_vals_by_session=fl_vals_by_session,
                    snore_vals_by_session=snore_vals_by_session,
                )
                nights.append(summary)
                if summary.is_compliant:
                    days_compliant += 1
            current += timedelta(days=1)

        n_nights = len(nights)
        compliance_pct = (
            (days_compliant / n_calendar * 100.0) if n_calendar > 0 else 0.0
        )
        return NightlyRangeSummary(
            date_start=date_start,
            date_end=date_end,
            device_id=resolved_device_id,
            compliance_threshold_hours=compliance_threshold_hours,
            n_calendar_nights=n_calendar,
            n_nights=n_nights,
            days_compliant=days_compliant,
            compliance_pct=compliance_pct,
            nights=nights,
        )
