"""Unit tests for get_device_capabilities chunked ID binding (#280).

SQLite caps bound parameters per statement, so get_device_capabilities chunks
its unbounded ``day_id`` / ``session_id`` IN-lists.  These tests pin
ID_CHUNK_SIZE=2 and seed days/sessions across the chunk boundary to prove:

- session-count is SUMMED across day chunks (S2),
- channel / event / setting-key DISTINCT sets are UNIONED across session
  chunks and returned sorted (S3/S4/S5),
- rx-key detection is unioned across chunks with the constant _RX_KEYS filter
  and the ``value IS NOT NULL`` guard both intact (S6).
"""

from __future__ import annotations

import uuid

from datetime import date, datetime, timedelta

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.services.breath_service import BreathService


async def _make_profile(db: AsyncSession) -> int:
    """Create a User + Profile, returning the profile id."""
    user = models.User(
        canonical_email=f"caps_{uuid.uuid4().hex[:8]}@test.example",
        role="admin",
    )
    db.add(user)
    await db.flush()
    profile = models.Profile(user_id=user.id, name="CapsTest")
    db.add(profile)
    await db.flush()
    return profile.id


async def _make_device(db: AsyncSession, profile_id: int) -> models.Device:
    device = models.Device(
        profile_id=profile_id,
        serial_number=f"SN_{uuid.uuid4().hex[:6]}",
        manufacturer="ResMed",
        model="AirCurve 11 VAuto",
        firmware_version="1.0",
    )
    db.add(device)
    await db.flush()
    return device


async def _add_day_with_sessions(
    db: AsyncSession,
    device_id: int,
    therapy_date: date,
    n_sessions: int,
) -> list[models.Session]:
    """Create a Day with ``n_sessions`` child Sessions, returning the sessions."""
    day = models.Day(device_id=device_id, date=therapy_date, session_count=n_sessions)
    db.add(day)
    await db.flush()
    start_dt = datetime(therapy_date.year, therapy_date.month, therapy_date.day, 22, 0)
    sessions: list[models.Session] = []
    for i in range(n_sessions):
        session = models.Session(
            device_id=device_id,
            day_id=day.id,
            device_session_id=f"SESS_{uuid.uuid4().hex[:8]}",
            start_time=start_dt + timedelta(hours=i),
            end_time=start_dt + timedelta(hours=i + 1),
            duration_seconds=3600.0,
        )
        db.add(session)
        sessions.append(session)
    await db.flush()
    return sessions


@pytest.mark.unit
class TestGetDeviceCapabilitiesChunked:
    async def test_capabilities_summed_and_unioned_across_chunks(
        self, async_db_session, monkeypatch
    ):
        """All chunked aggregates cross the ID_CHUNK_SIZE=2 boundary correctly.

        Three days (day_ids span two chunks) carry five sessions total
        (session_ids span three chunks).  Distinct waveform-types, event-types,
        and setting-keys are placed on the first and last sessions so a bug that
        processed only one chunk would drop values from the others.
        """
        monkeypatch.setattr("snore.utils.db_chunk.ID_CHUNK_SIZE", 2)

        profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)

        # 2 + 2 + 1 = 5 sessions across 3 days.  day_ids -> [d1,d2],[d3];
        # session_ids -> [s1,s2],[s3,s4],[s5].
        s1, s2 = await _add_day_with_sessions(
            async_db_session, dev.id, date(2025, 9, 1), 2
        )
        s3, _s4 = await _add_day_with_sessions(
            async_db_session, dev.id, date(2025, 9, 2), 2
        )
        (s5,) = await _add_day_with_sessions(
            async_db_session, dev.id, date(2025, 9, 3), 1
        )

        # Distinct DISTINCT-column values spread across first / middle / last
        # sessions so each spans a different session chunk.
        async_db_session.add_all(
            [
                models.Waveform(
                    session_id=s1.id,
                    waveform_type="flow",
                    sample_rate=1.0,
                    sample_count=1,
                    data_blob=b"\x00\x00\x00\x00",
                ),
                models.Waveform(
                    session_id=s3.id,
                    waveform_type="pressure",
                    sample_rate=1.0,
                    sample_count=1,
                    data_blob=b"\x00\x00\x00\x00",
                ),
                models.Waveform(
                    session_id=s5.id,
                    waveform_type="leak",
                    sample_rate=1.0,
                    sample_count=1,
                    data_blob=b"\x00\x00\x00\x00",
                ),
                models.Event(
                    session_id=s1.id,
                    event_type="OA",
                    start_time=s1.start_time + timedelta(minutes=5),
                    duration_seconds=10.0,
                ),
                models.Event(
                    session_id=s3.id,
                    event_type="CA",
                    start_time=s3.start_time + timedelta(minutes=5),
                    duration_seconds=10.0,
                ),
                models.Event(
                    session_id=s5.id,
                    event_type="H",
                    start_time=s5.start_time + timedelta(minutes=5),
                    duration_seconds=10.0,
                ),
                # rx key with a value (counts for rx_keys + all_setting_keys)
                models.Setting(session_id=s1.id, key="mode", value="auto"),
                # rx key with NULL value: in all_setting_keys, NOT in rx_keys
                models.Setting(session_id=s2.id, key="ipap", value=None),
                # non-rx key: in all_setting_keys, NOT in rx_keys
                models.Setting(session_id=s3.id, key="humidity_level", value="3"),
                # rx key with a value in the final chunk
                models.Setting(session_id=s5.id, key="epr_level", value="2"),
            ]
        )
        await async_db_session.flush()

        svc = BreathService(async_db_session, profile_id=profile_id)
        caps = await svc.get_device_capabilities(device_id=dev.id)

        # S2: session-count summed across day chunks (4 + 1)
        assert caps.session_count == 5
        assert caps.nights_with_data == 3

        # S3/S4/S5: DISTINCT sets unioned across session chunks, returned sorted
        assert caps.channels_present == ["flow", "leak", "pressure"]
        assert caps.event_types_present == ["CA", "H", "OA"]
        assert caps.all_setting_keys_present == [
            "epr_level",
            "humidity_level",
            "ipap",
            "mode",
        ]

        # S6: rx keys unioned across chunks; _RX_KEYS filter drops humidity_level,
        # value-not-null guard drops the null-valued ipap.
        assert caps.rx_keys_present == ["epr_level", "mode"]
