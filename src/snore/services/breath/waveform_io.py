"""Waveform-window and CA-analysis fetch seams (DB-touching)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.shared.versioning import (
    AlgorithmIdentity,
    AnalysisStatus,
    DayAnalysisStatus,
    NullReason,
    TimezoneStatus,
)
from snore.database import models

from ._core import _BreathServiceCore, _resolve_timezone
from .dtos import (
    CaAnalysisResult,
    MultiSessionAmbiguityError,
    RawCaAnalysis,
    RawCaEvent,
    RawCaSessionData,
    RawWaveformChannel,
    RawWaveformWindow,
    SessionCoverage,
    SessionSummary,
    WaveformChannelName,
    WaveformWindow,
    WaveformWindowRequest,
)


async def _fetch_waveform_blobs(
    db: AsyncSession,
    request: WaveformWindowRequest,
    session_id: int,
    session_start: datetime,
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN,
    timezone_name: str | None = None,
) -> RawWaveformWindow:
    """PRIVATE — fetch waveform blobs for a pre-resolved, already-owned session.

    Trusted internal helper: ownership has already been verified by the caller
    (via ``_resolve_range`` or ``fetch_waveform_window_raw``).  No ownership
    check or Session query is performed here.
    """

    requested_types = [ch.value for ch in request.channels]
    wf_stmt = select(models.Waveform).where(
        models.Waveform.session_id == session_id,
        models.Waveform.waveform_type.in_(requested_types),
    )
    wf_rows = (await db.execute(wf_stmt)).scalars().all()
    wf_by_type = {w.waveform_type: w for w in wf_rows}

    channels: list[RawWaveformChannel] = []
    missing: list[WaveformChannelName] = []
    for ch in request.channels:
        wf = wf_by_type.get(ch.value)
        if wf is None:
            missing.append(ch)
        else:
            channels.append(
                RawWaveformChannel(
                    waveform_type=ch,
                    unit=getattr(wf, "unit", None),
                    sample_rate=wf.sample_rate or 1.0,
                    sample_count=getattr(wf, "sample_count", 0),
                    raw_bytes=wf.data_blob or b"",
                )
            )

    return RawWaveformWindow(
        request=request,
        session_id=session_id,
        session_start_wall_clock=session_start,
        timezone_status=timezone_status,
        timezone_name=timezone_name,
        channels=channels,
        missing_channels=missing,
    )


async def fetch_waveform_window_raw(
    db: AsyncSession,
    profile_id: int,
    request: WaveformWindowRequest,
) -> RawWaveformWindow:
    """PUBLIC — fetch waveform blobs with profile-level ownership enforcement.

    Never closes db: the scope owner opens and closes the scope around this call.

    ``request.session_id`` must be set (direct callers must have a resolved session).
    Verifies ``Device.profile_id == profile_id`` via a join; raises ``ValueError``
    when the session is not found or is not owned by ``profile_id``.  Derives
    ``session_start`` from the DB row — never from caller-supplied data, so a
    forged anchor cannot shift window offsets.
    """

    if request.session_id is None:
        raise ValueError(
            "request.session_id must be set; direct callers of fetch_waveform_window_raw "
            "must resolve a session before calling this function"
        )

    # Full-tuple ownership query: Session + Device (profile) + Day (date) + optional device.
    # Ownership contract: the session must match profile_id, therapy_date, AND device_id.
    stmt = (
        select(models.Session.start_time)
        .join(models.Device, models.Session.device_id == models.Device.id)
        .join(models.Day, models.Session.day_id == models.Day.id)
        .where(
            models.Session.id == request.session_id,
            models.Device.profile_id == profile_id,
            models.Day.date == request.therapy_date,
        )
    )
    if request.device_id is not None:
        stmt = stmt.where(models.Session.device_id == request.device_id)
    row = (await db.execute(stmt)).one_or_none()

    if row is None:
        raise ValueError(
            f"Session {request.session_id} not found or not owned by "
            f"profile {profile_id} for date {request.therapy_date}"
        )

    session_start: datetime = row[0]
    tz_status, tz_name = await _resolve_timezone(db, profile_id)
    return await _fetch_waveform_blobs(
        db,
        request,
        request.session_id,
        session_start,
        timezone_status=tz_status,
        timezone_name=tz_name,
    )


class WaveformMixin(_BreathServiceCore):
    """Waveform-window and CA-analysis service methods."""

    async def fetch_waveform_window(
        self, request: WaveformWindowRequest
    ) -> RawWaveformWindow:
        """Resolve, validate, and fetch raw waveform blobs for a window request.

        MCP raw/render seam: the fetch step runs
        inside the caller's DB scope while ``compute_waveform_window`` (pure, CPU-only)
        runs after the scope closes.  Direct callers that need the raw bytes or want
        to render a PNG call this method, then pass the returned ``RawWaveformWindow``
        to ``compute_waveform_window`` independently.

        Raises ``DeviceAmbiguityError`` for multi-device profiles with no device_id,
        ``DeviceNotOwnedError`` for a foreign device_id, ``ValueError`` when an
        explicit session_id is provided but the date has no sessions, and
        ``MultiSessionAmbiguityError`` when the date has multiple sessions and no
        session_id was specified.
        """
        from snore.services.breath_service import (  # noqa: PLC0415
            _fetch_waveform_blobs,
        )

        resolved_device_id, sessions_by_date = await self._resolve_range(
            request.therapy_date, request.therapy_date, request.device_id
        )
        day_sessions = sessions_by_date.get(request.therapy_date, [])

        tz_status, tz_name = await self.resolve_timezone()

        # Validate explicit session_id BEFORE the empty-day return.
        # An owned device on an empty date with an explicit session_id must raise,
        # not silently return a synthetic empty window.
        if not day_sessions:
            if request.session_id is not None:
                raise ValueError(
                    f"Session {request.session_id} not found for date "
                    f"{request.therapy_date} on device {resolved_device_id}"
                )
            return RawWaveformWindow(
                request=request,
                session_id=0,
                session_start_wall_clock=datetime.min,
                timezone_status=tz_status,
                timezone_name=tz_name,
                channels=[],
                missing_channels=list(request.channels),
            )

        if request.session_id is not None:
            # Verify the provided session_id belongs to the resolved device
            session_ids = {s.id for s in day_sessions}
            if request.session_id not in session_ids:
                raise ValueError(
                    f"Session {request.session_id} not found for date "
                    f"{request.therapy_date} on device {resolved_device_id}"
                )
            session_row = next(s for s in day_sessions if s.id == request.session_id)
        elif len(day_sessions) > 1:
            raise MultiSessionAmbiguityError(
                therapy_date=request.therapy_date,
                device_id=resolved_device_id,
                sessions=[
                    SessionSummary(
                        session_id=s.id,
                        start_wall_clock=s.start_time,
                        timezone_status=tz_status,
                        timezone_name=tz_name,
                        duration_seconds=s.duration_seconds or 0.0,
                    )
                    for s in day_sessions
                ],
            )
        else:
            session_row = day_sessions[0]

        resolved_request = request.model_copy(
            update={"device_id": resolved_device_id, "session_id": session_row.id}
        )
        return await _fetch_waveform_blobs(
            self._db,
            resolved_request,
            session_row.id,
            session_row.start_time,
            timezone_status=tz_status,
            timezone_name=tz_name,
        )

    async def get_waveform_window(
        self, request: WaveformWindowRequest
    ) -> WaveformWindow:
        """Convenience orchestrator: resolve → fetch blobs → compute. Never closes self._db.

        Uses ``_resolve_range`` for device validation and session selection (raises
        ``DeviceAmbiguityError`` for multi-device, ``DeviceNotOwnedError`` for foreign
        device_id), then delegates to ``fetch_waveform_window`` (the MCP seam) and
        applies ``compute_waveform_window`` (pure) to produce the final DTO.
        """
        from snore.services.breath_service import (  # noqa: PLC0415
            compute_waveform_window,
        )

        raw = await self.fetch_waveform_window(request)
        return compute_waveform_window(raw)

    async def fetch_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> RawCaAnalysis:
        """Fetch all DB data for CA analysis (in-scope fetch seam).

        Resolves device, iterates sessions, pre-fetches MV/THERAPY_PRESSURE/EPAP
        waveform blobs (one fetch per session), queries CA events, and loads
        OK-session programmatic_result_json for PB% computation.

        Returns a ``RawCaAnalysis`` carrying every input that
        ``compute_ca_analysis`` needs — no ORM handles or DB sessions escape.
        Empty session_data signals an empty day; ``compute_ca_analysis`` maps it
        to the NOT_RUN sentinel result.

        ``DeviceAmbiguityError`` and ``DeviceNotOwnedError`` propagate to the
        caller unchanged.
        """
        from snore.services.breath_service import (  # noqa: PLC0415
            _fetch_waveform_blobs,
        )

        # Resolve device via _resolve_range (DeviceAmbiguityError propagates to caller)
        resolved_device_id, sessions_by_date = await self._resolve_range(
            therapy_date, therapy_date, device_id
        )
        all_day_sessions = sessions_by_date.get(therapy_date, [])

        tz_status, tz_name = await self.resolve_timezone()

        if not all_day_sessions:
            return RawCaAnalysis(
                therapy_date=therapy_date,
                device_id=resolved_device_id,
                session_data=[],
                timezone_status=tz_status,
                timezone_name=tz_name,
                day_status=DayAnalysisStatus.NOT_RUN,
                algorithm_identity=None,
                null_reason=NullReason.ANALYSIS_NOT_RUN,
            )

        # Build coverage; identify OK sessions
        coverage: list[SessionCoverage] = []
        identities_for_reduce: list[AlgorithmIdentity] = []
        ok_session_ids: set[int] = set()
        ar_id_by_session: dict[int, int] = {}
        algo_identity: AlgorithmIdentity | None = None

        for sess in all_day_sessions:
            status, algo, ar_id = await self._latest_analysis_for_session(sess.id)
            cov = SessionCoverage(
                session_id=sess.id, analysis_status=status, algo_versions=algo
            )
            coverage.append(cov)
            if status == AnalysisStatus.OK and algo is not None and ar_id is not None:
                ok_session_ids.add(sess.id)
                ar_id_by_session[sess.id] = ar_id
                identities_for_reduce.append(algo.identity)
                algo_identity = algo.identity

        ca_day_status = self._reduce_day_status(coverage, identities_for_reduce)

        # MIXED_VERSION contract: no single identity is representative → None
        if ca_day_status == DayAnalysisStatus.MIXED_VERSION:
            algo_identity = None

        # Map day_status → null_reason for the result
        if ca_day_status == DayAnalysisStatus.OK:
            ca_null_reason: NullReason | None = None
        elif ca_day_status == DayAnalysisStatus.STALE:
            ca_null_reason = NullReason.ANALYSIS_STALE
        elif ca_day_status == DayAnalysisStatus.MIXED_VERSION:
            # Conflicting algo identities → ALGO_VERSION_MISMATCH
            ca_null_reason = NullReason.ALGO_VERSION_MISMATCH
        elif ca_day_status == DayAnalysisStatus.PARTIAL:
            ca_null_reason = None
        else:
            ca_null_reason = NullReason.ANALYSIS_NOT_RUN

        night_level_refused = ca_day_status == DayAnalysisStatus.MIXED_VERSION

        # Coverage lookup for DTO construction
        cov_by_session: dict[int, SessionCoverage] = {c.session_id: c for c in coverage}

        # Fetch per-session data
        session_data: list[RawCaSessionData] = []
        for session_row in all_day_sessions:
            session_id = session_row.id
            session_start = session_row.start_time
            session_duration_s = session_row.duration_seconds or 0.0
            is_ok = session_id in ok_session_ids

            # Pre-fetch MV + THERAPY_PRESSURE + EPAP blobs once per session.
            # Corrupt blobs still raise ValueError — never silently skipped.
            session_cap = max(session_duration_s, 1.0)
            pre_raw = await _fetch_waveform_blobs(
                self._db,
                WaveformWindowRequest(
                    therapy_date=therapy_date,
                    session_id=session_id,
                    device_id=resolved_device_id,
                    channels=[
                        WaveformChannelName.MV,
                        WaveformChannelName.THERAPY_PRESSURE,
                        WaveformChannelName.EPAP,
                    ],
                    offset_start=0.0,
                    offset_end=session_cap,
                    window_cap_seconds=session_cap,
                ),
                session_id,
                session_start,
            )

            # Fetch CA events for this session
            ca_rows = (
                (
                    await self._db.execute(
                        select(models.Event)
                        .where(
                            models.Event.session_id == session_id,
                            models.Event.event_type == "CA",
                        )
                        .order_by(models.Event.start_time)
                    )
                )
                .scalars()
                .all()
            )
            raw_events = [
                RawCaEvent(
                    start_time=ev.start_time,
                    duration_seconds=ev.duration_seconds,
                )
                for ev in ca_rows
            ]

            # No device MV channel → fetch FLOW blobs for the flow-derived MV
            # fallback (compute_ca_analysis runs derive_mv_from_flow on them).
            # Derived MV is only consumed for per-event metrics (CA events
            # present) or rolling-variance bins (analysis-OK session), so skip
            # the fetch entirely when neither consumer exists.
            flow_raw: RawWaveformWindow | None = None
            if WaveformChannelName.MV in pre_raw.missing_channels and (
                raw_events or is_ok
            ):
                flow_raw = await _fetch_waveform_blobs(
                    self._db,
                    WaveformWindowRequest(
                        therapy_date=therapy_date,
                        session_id=session_id,
                        device_id=resolved_device_id,
                        channels=[WaveformChannelName.FLOW],
                        offset_start=0.0,
                        offset_end=session_cap,
                        window_cap_seconds=session_cap,
                    ),
                    session_id,
                    session_start,
                )

            # Load programmatic_result_json for OK sessions (PB% computation)
            pb_json: dict[str, Any] | None = None
            if is_ok and not night_level_refused:
                ar_id = ar_id_by_session.get(session_id)
                if ar_id is not None:
                    ar_row = (
                        (
                            await self._db.execute(
                                select(models.AnalysisResult).where(
                                    models.AnalysisResult.id == ar_id
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if ar_row is not None and ar_row.programmatic_result_json:
                        pb_json = ar_row.programmatic_result_json

            session_data.append(
                RawCaSessionData(
                    session_id=session_id,
                    session_start=session_start,
                    duration_seconds=session_duration_s,
                    coverage=cov_by_session[session_id],
                    is_ok=is_ok,
                    pre_waveform=pre_raw,
                    flow_waveform=flow_raw,
                    ca_events=raw_events,
                    pb_json=pb_json,
                )
            )

        return RawCaAnalysis(
            therapy_date=therapy_date,
            device_id=resolved_device_id,
            session_data=session_data,
            timezone_status=tz_status,
            timezone_name=tz_name,
            day_status=ca_day_status,
            algorithm_identity=algo_identity,
            null_reason=ca_null_reason,
        )

    async def get_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> CaAnalysisResult:
        """Convenience orchestrator: fetch CA data → compute CA analysis. Never closes self._db.

        Uses ``fetch_ca_analysis`` (in-scope) to collect all DB data, then applies
        ``compute_ca_analysis`` (pure) to produce the final ``CaAnalysisResult``.
        See ``compute_ca_analysis`` for the numpy/statistics implementation.
        """
        from snore.services.breath_service import compute_ca_analysis  # noqa: PLC0415

        raw = await self.fetch_ca_analysis(
            therapy_date=therapy_date, device_id=device_id
        )
        return compute_ca_analysis(raw)
