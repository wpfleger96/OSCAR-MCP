"""Database management commands."""

from __future__ import annotations

import asyncio

from datetime import date, timedelta
from pathlib import Path

import click

from snore.auth.factory import resolve_cli_profile_id, resolve_local_profile_id
from snore.cli.decorators import actor_options, db_option, db_session
from snore.cli.display import (
    ICON_STATS,
    console,
    print_error,
    print_footer,
    print_header,
    print_kv,
    print_subsection,
    print_success,
    print_warning,
)
from snore.constants import DEFAULT_DATABASE_PATH
from snore.database.models import Base
from snore.database.session import cleanup_database, init_database, session_scope
from snore.services.database_service import DatabaseService


@click.group()
def db() -> None:
    """Database management commands."""
    pass


@db.command()
@db_option
def init(db: str | None) -> None:
    """Initialize database and apply pending migrations."""
    db_path = Path(db).expanduser() if db else Path(DEFAULT_DATABASE_PATH)
    db_existed = db_path.exists()

    asyncio.run(init_database(str(db_path)))

    table_names = sorted(Base.metadata.tables.keys())

    if db_existed:
        print_success(f"Database already initialized at {db_path}")
        console.print("\nVerified tables:")
    else:
        print_success(f"Created new database at {db_path}")
        console.print("\nInitialized tables:")

    for table_name in table_names:
        console.print(f"    - {table_name}")

    if db_existed:
        console.print("\nDatabase is up to date")


@db.command("stats")
@db_option
@actor_options
def db_stats(db: str | None, actor_user: str | None, actor_profile: str | None) -> None:
    """Show database statistics."""
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    async def _run() -> None:
        async with db_session(db) as session:
            profile_id = await resolve_cli_profile_id(
                session, actor_user, actor_profile
            )
            service = DatabaseService(session, profile_id)
            stats = await service.get_stats(str(db_path))

            print_header("Database Statistics", ICON_STATS)
            print_kv("Database", str(stats.db_path), indent=0)
            print_kv("Size", f"{stats.size_mb:.1f} MB", indent=0)

            print_subsection("Row Counts")
            print_kv("Profiles", str(stats.profile_count))
            print_kv("Devices", str(stats.device_count))
            print_kv("Sessions", str(stats.session_count))
            print_kv("Days", str(stats.day_count))
            print_kv("Events", str(stats.event_count))
            print_kv("Waveforms", str(stats.waveform_count))
            print_kv("Analysis Results", str(stats.analysis_count))
            print_kv("Detected Patterns", str(stats.pattern_count))

            print_subsection("Data Coverage")
            print_kv(
                "Sessions with waveforms",
                f"{stats.sessions_with_waveforms}/{stats.session_count} ({stats.waveform_coverage_pct:.1f}%)",
            )
            print_kv(
                "Sessions with events",
                f"{stats.sessions_with_events}/{stats.session_count} ({stats.event_coverage_pct:.1f}%)",
            )
            print_kv(
                "Sessions analyzed",
                f"{stats.analysis_count}/{stats.session_count} ({stats.analysis_coverage_pct:.1f}%)",
            )

            if stats.first_session and stats.last_session:
                console.print(
                    f"\nDate range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
                )

            print_footer()

    asyncio.run(_run())


@db.command()
@db_option
@click.confirmation_option(prompt="Are you sure you want to vacuum the database?")
def vacuum(db: str | None) -> None:
    """Optimize database (reclaim space after deletions)."""
    db_path = Path(db) if db else Path(DEFAULT_DATABASE_PATH)

    console.print("Vacuuming database...")

    async def _run() -> None:
        async with db_session(db) as session:
            service = DatabaseService(session, await resolve_local_profile_id(session))
            result = service.vacuum_sqlite(str(db_path))
            print_success(
                f"Database vacuumed successfully ({result.size_before_mb:.1f} MB → {result.size_after_mb:.1f} MB)"
            )

    asyncio.run(_run())


@db.command()
@db_option
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def drop(db: str | None, force: bool) -> None:
    """Drop database (permanently delete all CPAP data)."""
    db_path = Path(db).expanduser() if db else Path(DEFAULT_DATABASE_PATH)

    if not db_path.exists():
        console.print(f"Database does not exist at {db_path}")
        return

    try:
        asyncio.run(init_database(str(db_path)))

        async def _show_stats() -> None:
            async with session_scope() as session:
                profile_id = await resolve_local_profile_id(session)
                service = DatabaseService(session, profile_id)
                stats = await service.get_stats(str(db_path))

                console.print(f"\nDatabase: {db_path}")
                console.print(f"Size: {stats.size_mb:.1f} MB")
                console.print(f"Devices: {stats.device_count}")
                console.print(f"Sessions: {stats.session_count}")
                console.print(f"Events: {stats.event_count:,}")

                if stats.first_session and stats.last_session:
                    console.print(
                        f"Date range: {stats.first_session:%Y-%m-%d} to {stats.last_session:%Y-%m-%d}"
                    )

        asyncio.run(_show_stats())

    except Exception as e:
        print_warning(f"Could not read database stats: {e}")

    if not force:
        print_warning("WARNING: This will permanently delete all CPAP data!")
        if not click.confirm(
            "Are you sure you want to drop the database?", default=False
        ):
            console.print("Database drop cancelled")
            return

    try:
        asyncio.run(cleanup_database())
    except Exception as e:
        print_warning(f"Warning during cleanup: {e}")

    try:
        if db_path.exists():
            db_path.unlink()
            print_success(f"Deleted database: {db_path}")

        for ext in ["-wal", "-shm"]:
            wal_file = Path(str(db_path) + ext)
            if wal_file.exists():
                wal_file.unlink()
                print_success(f"Deleted: {wal_file.name}")

        console.print("\nDatabase dropped successfully")

    except Exception as e:
        raise click.ClickException(f"Error dropping database: {e}") from e


@db.command("scrub-demo")
@db_option
@click.option(
    "--source-profile",
    "source_profile_id",
    required=True,
    type=int,
    help="Profile ID to copy data from",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def scrub_demo(db: str | None, source_profile_id: int, yes: bool) -> None:
    """Populate the demo account from a source profile.

    Reproducible, idempotent scrub: running twice replaces prior demo data.

    PII scrubs applied:
    - profiles: name='Demo', username/first_name/last_name -> None,
      date_of_birth -> None, height_cm -> None, settings -> {}
    - devices: serial_number -> 'DEMO-NNN', firmware/hardware/product_code -> None;
      manufacturer and model are kept (device product names, not PII).
    - sessions: import_source -> 'demo'
    - settings: all keys and values are copied unchanged. Device settings are
      an enum-like fixed set (pressure, ramp, EPR, etc.) — therapy configuration
      data, not identity PII. Preserving them lets demo viewers see realistic
      therapy settings.
    - detected_patterns: notes -> None
    - Date rotation: all dates/datetimes shifted by a consistent whole-day
      offset so the most recent source day lands exactly 7 days before today.
    - Raw backup files are NOT copied (filesystem isolation).
    """
    if not yes:
        click.confirm(
            f"Scrub demo data from source profile {source_profile_id}? "
            "This will replace all existing demo data.",
            abort=True,
        )

    async def _run() -> None:
        async with db_session(db) as session:
            await _do_scrub_demo(session, source_profile_id)

    asyncio.run(_run())


async def _do_scrub_demo(session: object, source_profile_id: int) -> None:
    """Async implementation of the scrub-demo command."""
    from datetime import datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    from snore.database.models import (  # noqa: PLC0415
        AnalysisResult,
        Breath,
        Day,
        DetectedPattern,
        Device,
        Event,
        Profile,
        Setting,
        Statistics,
        User,
        Waveform,
    )
    from snore.database.models import (
        Session as DbSession,
    )

    db: AsyncSession = session  # type: ignore[assignment]

    # ---- 1. Verify source profile exists ----
    source_profile = await db.get(Profile, source_profile_id)
    if source_profile is None:
        raise click.ClickException(f"Source profile {source_profile_id} not found")

    # ---- 2. Find-or-create demo user ----
    # Each query uses a unique variable name so mypy can track the return type.
    demo_email = "demo@snore.local"
    user_q = select(User).where(User.canonical_email == demo_email)
    demo_user: User | None = (await db.execute(user_q)).scalars().first()

    if demo_user is None:
        demo_user = User(
            canonical_email=demo_email,
            display_name="Demo",
            role="demo",
            password_hash=None,
            session_version=0,
        )
        db.add(demo_user)
        await db.flush()
        console.print(f"Created demo user (id={demo_user.id})")
    else:
        console.print(f"Using existing demo user (id={demo_user.id})")

    assert demo_user is not None  # narrowing for mypy
    # ---- 3. Find-or-create demo profile ----
    profile_q = select(Profile).where(
        Profile.user_id == demo_user.id,
        Profile.name == "Demo",
    )
    demo_profile: Profile | None = (await db.execute(profile_q)).scalars().first()

    if demo_profile is None:
        demo_profile = Profile(
            user_id=demo_user.id,
            name="Demo",
            username=None,
            first_name=None,
            last_name=None,
            date_of_birth=None,
            height_cm=None,
            settings={},
        )
        db.add(demo_profile)
        await db.flush()
        demo_user.default_profile_id = demo_profile.id
        await db.flush()
        console.print(f"Created demo profile (id={demo_profile.id})")
    else:
        console.print(f"Replacing existing demo profile data (id={demo_profile.id})")
        # Delete existing device chain (cascade handles sessions, events, etc.)
        cleanup_dev_q = select(Device).where(Device.profile_id == demo_profile.id)
        existing_devices = (await db.execute(cleanup_dev_q)).scalars().all()
        for dev in existing_devices:
            await db.delete(dev)
        await db.flush()

    assert demo_profile is not None  # narrowing for mypy
    # Ensure demo_user.default_profile_id points to the demo profile.
    if demo_user.default_profile_id != demo_profile.id:
        demo_user.default_profile_id = demo_profile.id
        await db.flush()

    # ---- 4. Compute date shift ----
    # Find the most recent date across all source days.
    max_date_q = (
        select(Day.date)
        .join(Device, Day.device_id == Device.id)
        .where(Device.profile_id == source_profile_id)
        .order_by(Day.date.desc())
        .limit(1)
    )
    max_date_row = (await db.execute(max_date_q)).scalars().first()

    if max_date_row is None:
        console.print("[yellow]Source profile has no days — nothing to copy.[/yellow]")
        return

    max_date: date = max_date_row
    today = date.today()
    target_date = today - timedelta(days=7)
    day_offset = target_date - max_date
    console.print(
        f"Date shift: {day_offset.days:+d} days "
        f"(most recent source day {max_date} -> {target_date})"
    )

    def shift_date(d: date | None) -> date | None:
        return d + day_offset if d is not None else None

    def shift_dt(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt + day_offset

    # ---- 5. Copy devices ----
    source_dev_q = select(Device).where(Device.profile_id == source_profile_id)
    source_devices = (await db.execute(source_dev_q)).scalars().all()

    counts: dict[str, int] = {
        "devices": 0,
        "days": 0,
        "sessions": 0,
        "waveforms": 0,
        "events": 0,
        "statistics": 0,
        "settings": 0,
        "analysis_results": 0,
        "detected_patterns": 0,
        "breaths": 0,
    }

    # Map source device id -> demo device id
    device_id_map: dict[int, int] = {}
    # Map source day id -> demo day id (needed for session.day_id)
    day_id_map: dict[int, int] = {}

    for dev_idx, src_dev in enumerate(source_devices):
        demo_serial = f"DEMO-{dev_idx + 1:03d}"
        demo_dev = Device(
            profile_id=demo_profile.id,
            manufacturer=src_dev.manufacturer,
            model=src_dev.model,
            serial_number=demo_serial,
            firmware_version=None,
            hardware_version=None,
            product_code=None,
        )
        db.add(demo_dev)
        await db.flush()
        device_id_map[src_dev.id] = demo_dev.id
        counts["devices"] += 1

        # Copy days for this device
        days_q = select(Day).where(Day.device_id == src_dev.id)
        src_days = (await db.execute(days_q)).scalars().all()
        for src_day in src_days:
            demo_day = Day(
                device_id=demo_dev.id,
                date=shift_date(src_day.date),
                session_count=src_day.session_count,
                total_therapy_hours=src_day.total_therapy_hours,
                obstructive_apneas=src_day.obstructive_apneas,
                central_apneas=src_day.central_apneas,
                hypopneas=src_day.hypopneas,
                reras=src_day.reras,
                ahi=src_day.ahi,
                oai=src_day.oai,
                cai=src_day.cai,
                hi=src_day.hi,
                pressure_min=src_day.pressure_min,
                pressure_max=src_day.pressure_max,
                pressure_median=src_day.pressure_median,
                pressure_mean=src_day.pressure_mean,
                pressure_95th=src_day.pressure_95th,
                epap_min=src_day.epap_min,
                epap_max=src_day.epap_max,
                epap_median=src_day.epap_median,
                epap_mean=src_day.epap_mean,
                epap_95th=src_day.epap_95th,
                leak_min=src_day.leak_min,
                leak_max=src_day.leak_max,
                leak_median=src_day.leak_median,
                leak_mean=src_day.leak_mean,
                leak_95th=src_day.leak_95th,
                spo2_min=src_day.spo2_min,
                spo2_max=src_day.spo2_max,
                spo2_mean=src_day.spo2_mean,
            )
            db.add(demo_day)
            await db.flush()
            day_id_map[src_day.id] = demo_day.id
            counts["days"] += 1

    # Copy sessions + children
    # Map source session id -> demo session id (for breaths denorm FK)
    session_id_map: dict[int, int] = {}

    for src_dev in source_devices:
        demo_device_id = device_id_map[src_dev.id]

        sessions_q = select(DbSession).where(DbSession.device_id == src_dev.id)
        src_sessions = (await db.execute(sessions_q)).scalars().all()

        for src_sess in src_sessions:
            demo_day_id = day_id_map.get(src_sess.day_id) if src_sess.day_id else None

            demo_sess = DbSession(
                device_id=demo_device_id,
                day_id=demo_day_id,
                device_session_id=src_sess.device_session_id,
                start_time=shift_dt(src_sess.start_time),
                end_time=shift_dt(src_sess.end_time),
                duration_seconds=src_sess.duration_seconds,
                therapy_mode=src_sess.therapy_mode,
                import_source="demo",
                parser_version=src_sess.parser_version,
                data_quality_notes=src_sess.data_quality_notes,
                has_waveform_data=src_sess.has_waveform_data,
                has_event_data=src_sess.has_event_data,
                has_statistics=src_sess.has_statistics,
                enabled=src_sess.enabled,
            )
            db.add(demo_sess)
            await db.flush()
            session_id_map[src_sess.id] = demo_sess.id
            counts["sessions"] += 1

            # Copy waveforms (binary data blobs — no PII)
            wf_q = select(Waveform).where(Waveform.session_id == src_sess.id)
            src_waveforms = (await db.execute(wf_q)).scalars().all()
            for src_wf in src_waveforms:
                demo_wf = Waveform(
                    session_id=demo_sess.id,
                    waveform_type=src_wf.waveform_type,
                    sample_rate=src_wf.sample_rate,
                    unit=src_wf.unit,
                    min_value=src_wf.min_value,
                    max_value=src_wf.max_value,
                    mean_value=src_wf.mean_value,
                    data_blob=src_wf.data_blob,
                    sample_count=src_wf.sample_count,
                )
                db.add(demo_wf)
                counts["waveforms"] += 1

            # Copy events
            events_q = select(Event).where(Event.session_id == src_sess.id)
            src_events = (await db.execute(events_q)).scalars().all()
            for src_evt in src_events:
                demo_evt = Event(
                    session_id=demo_sess.id,
                    event_type=src_evt.event_type,
                    start_time=shift_dt(src_evt.start_time),
                    duration_seconds=src_evt.duration_seconds,
                    spo2_drop=src_evt.spo2_drop,
                    peak_flow_limitation=src_evt.peak_flow_limitation,
                )
                db.add(demo_evt)
                counts["events"] += 1

            # Copy statistics
            stats_q = select(Statistics).where(Statistics.session_id == src_sess.id)
            src_stats = (await db.execute(stats_q)).scalars().first()
            if src_stats is not None:
                demo_stats = Statistics(
                    session_id=demo_sess.id,
                    obstructive_apneas=src_stats.obstructive_apneas,
                    central_apneas=src_stats.central_apneas,
                    mixed_apneas=src_stats.mixed_apneas,
                    hypopneas=src_stats.hypopneas,
                    reras=src_stats.reras,
                    flow_limitations=src_stats.flow_limitations,
                    ahi=src_stats.ahi,
                    oai=src_stats.oai,
                    cai=src_stats.cai,
                    hi=src_stats.hi,
                    rei=src_stats.rei,
                    pressure_min=src_stats.pressure_min,
                    pressure_max=src_stats.pressure_max,
                    pressure_median=src_stats.pressure_median,
                    pressure_mean=src_stats.pressure_mean,
                    pressure_95th=src_stats.pressure_95th,
                    epap_min=src_stats.epap_min,
                    epap_max=src_stats.epap_max,
                    epap_median=src_stats.epap_median,
                    epap_mean=src_stats.epap_mean,
                    epap_95th=src_stats.epap_95th,
                    ipap_median=src_stats.ipap_median,
                    ipap_95th=src_stats.ipap_95th,
                    ipap_max=src_stats.ipap_max,
                    leak_min=src_stats.leak_min,
                    leak_max=src_stats.leak_max,
                    leak_median=src_stats.leak_median,
                    leak_mean=src_stats.leak_mean,
                    leak_95th=src_stats.leak_95th,
                    leak_percentile_70=src_stats.leak_percentile_70,
                    respiratory_rate_min=src_stats.respiratory_rate_min,
                    respiratory_rate_max=src_stats.respiratory_rate_max,
                    respiratory_rate_mean=src_stats.respiratory_rate_mean,
                    tidal_volume_min=src_stats.tidal_volume_min,
                    tidal_volume_max=src_stats.tidal_volume_max,
                    tidal_volume_mean=src_stats.tidal_volume_mean,
                    minute_ventilation_min=src_stats.minute_ventilation_min,
                    minute_ventilation_max=src_stats.minute_ventilation_max,
                    minute_ventilation_mean=src_stats.minute_ventilation_mean,
                    spo2_min=src_stats.spo2_min,
                    spo2_max=src_stats.spo2_max,
                    spo2_mean=src_stats.spo2_mean,
                    spo2_time_below_90=src_stats.spo2_time_below_90,
                    pulse_min=src_stats.pulse_min,
                    pulse_max=src_stats.pulse_max,
                    pulse_mean=src_stats.pulse_mean,
                    usage_hours=src_stats.usage_hours,
                )
                db.add(demo_stats)
                counts["statistics"] += 1

            # Copy settings
            settings_q = select(Setting).where(Setting.session_id == src_sess.id)
            src_settings = (await db.execute(settings_q)).scalars().all()
            for src_setting in src_settings:
                demo_setting = Setting(
                    session_id=demo_sess.id,
                    key=src_setting.key,
                    value=src_setting.value,
                )
                db.add(demo_setting)
                counts["settings"] += 1

            # Copy analysis results + children
            ar_q = select(AnalysisResult).where(
                AnalysisResult.session_id == src_sess.id
            )
            src_analyses = (await db.execute(ar_q)).scalars().all()
            for src_ar in src_analyses:
                demo_ar = AnalysisResult(
                    session_id=demo_sess.id,
                    timestamp_start=shift_dt(src_ar.timestamp_start),
                    timestamp_end=shift_dt(src_ar.timestamp_end),
                    programmatic_result_json=src_ar.programmatic_result_json,
                    processing_time_ms=src_ar.processing_time_ms,
                    engine_versions_json=src_ar.engine_versions_json,
                )
                db.add(demo_ar)
                await db.flush()
                counts["analysis_results"] += 1

                # Copy detected patterns
                patterns_q = select(DetectedPattern).where(
                    DetectedPattern.analysis_result_id == src_ar.id
                )
                src_patterns = (await db.execute(patterns_q)).scalars().all()
                for src_pat in src_patterns:
                    demo_pat = DetectedPattern(
                        analysis_result_id=demo_ar.id,
                        pattern_id=src_pat.pattern_id,
                        start_time=shift_dt(src_pat.start_time),
                        duration=src_pat.duration,
                        confidence=src_pat.confidence,
                        detected_by=src_pat.detected_by,
                        metrics_json=src_pat.metrics_json,
                        notes=None,  # PII scrub
                    )
                    db.add(demo_pat)
                    counts["detected_patterns"] += 1

                # Copy breaths (offsets are session-relative seconds, no date shift)
                breaths_q = select(Breath).where(Breath.analysis_result_id == src_ar.id)
                src_breaths = (await db.execute(breaths_q)).scalars().all()
                for src_breath in src_breaths:
                    demo_breath = Breath(
                        analysis_result_id=demo_ar.id,
                        session_id=demo_sess.id,
                        breath_number=src_breath.breath_number,
                        start_offset_s=src_breath.start_offset_s,
                        end_offset_s=src_breath.end_offset_s,
                        inspiration_time_s=src_breath.inspiration_time_s,
                        expiration_time_s=src_breath.expiration_time_s,
                        total_time_s=src_breath.total_time_s,
                        i_e_ratio=src_breath.i_e_ratio,
                        duty_cycle=src_breath.duty_cycle,
                        peak_flow_lpm=src_breath.peak_flow_lpm,
                        peak_exp_flow_lpm=src_breath.peak_exp_flow_lpm,
                        tidal_volume_ml=src_breath.tidal_volume_ml,
                        respiratory_rate_rolling=src_breath.respiratory_rate_rolling,
                        flatness_index=src_breath.flatness_index,
                        mid_insp_flattening=src_breath.mid_insp_flattening,
                        flow_class=src_breath.flow_class,
                        flow_confidence=src_breath.flow_confidence,
                        is_recovery_breath=src_breath.is_recovery_breath,
                        inferred_trigger_type=src_breath.inferred_trigger_type,
                        trigger_confidence=src_breath.trigger_confidence,
                        inferred_cycle_type=src_breath.inferred_cycle_type,
                        cycle_confidence=src_breath.cycle_confidence,
                        trigger_cycle_applicable=src_breath.trigger_cycle_applicable,
                        trigger_cycle_reason=src_breath.trigger_cycle_reason,
                        leak_valid=src_breath.leak_valid,
                        leak_valid_reason=src_breath.leak_valid_reason,
                        ramp_active=src_breath.ramp_active,
                        ramp_active_reason=src_breath.ramp_active_reason,
                        mask_off=src_breath.mask_off,
                        mask_off_reason=src_breath.mask_off_reason,
                    )
                    db.add(demo_breath)
                    counts["breaths"] += 1

            await db.flush()

    await db.flush()

    # ---- 6. Post-scrub assertions ----
    # a. No source serial numbers in demo devices
    assert_device_stmt = select(Device).where(Device.profile_id == demo_profile.id)
    demo_devices_check = (await db.execute(assert_device_stmt)).scalars().all()
    source_serials = {d.serial_number for d in source_devices}
    for dev in demo_devices_check:
        assert dev.serial_number not in source_serials, (
            f"ASSERTION FAILED: demo device still has source serial {dev.serial_number!r}"
        )

    # b. Demo profile has no PII fields
    await db.refresh(demo_profile)
    assert demo_profile.first_name is None, (
        "ASSERTION FAILED: demo profile.first_name is not None"
    )
    assert demo_profile.last_name is None, (
        "ASSERTION FAILED: demo profile.last_name is not None"
    )
    assert demo_profile.date_of_birth is None, (
        "ASSERTION FAILED: demo profile.date_of_birth is not None"
    )

    # c. No detected_patterns.notes
    assert_pattern_stmt = (
        select(DetectedPattern)
        .join(AnalysisResult, DetectedPattern.analysis_result_id == AnalysisResult.id)
        .join(DbSession, AnalysisResult.session_id == DbSession.id)
        .join(Device, DbSession.device_id == Device.id)
        .where(Device.profile_id == demo_profile.id)
        .where(DetectedPattern.notes.is_not(None))
    )
    bad_patterns = (await db.execute(assert_pattern_stmt)).scalars().all()
    assert not bad_patterns, (
        f"ASSERTION FAILED: {len(bad_patterns)} demo detected_patterns still have notes"
    )

    # d. No raw backup files for demo profile
    from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

    raw_dir = Path(DEFAULT_RAW_BACKUP_DIR) / str(demo_profile.id)
    assert not raw_dir.exists(), (
        f"ASSERTION FAILED: raw backup dir exists for demo profile: {raw_dir}"
    )

    # ---- 7. Summary ----
    print_success("Scrub-demo complete")
    print_subsection("Rows copied")
    for table, count in counts.items():
        print_kv(table, str(count))
    print_kv("day offset", f"{day_offset.days:+d} days")


@db.command("purge-quarantine")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def db_purge_quarantine(yes: bool) -> None:
    """Purge all quarantined profile raw data (offline operator command).

    Quarantined directories are left behind when a profile deletion saga is
    interrupted (e.g. crash after rename but before cascade-delete).  This
    command removes them permanently.

    The API server must not be running when this command is used.
    """
    if not yes:
        click.confirm(
            "Purge all quarantined profile raw data? This cannot be undone. "
            "Ensure the API server is not running.",
            abort=True,
        )

    from snore.services.profile_service import DeletionSaga  # noqa: PLC0415
    from snore.services.writer_lease import WriterLeaseError  # noqa: PLC0415

    saga = DeletionSaga()
    try:
        saga.purge_quarantine()
        print_success("Quarantine purged successfully")
    except WriterLeaseError as e:
        print_error(str(e))
