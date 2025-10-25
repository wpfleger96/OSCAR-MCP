"""
Session import functionality for converting UnifiedSession to database records.

Handles the complete import process including waveforms, events, and statistics.
"""

import logging
import json
import numpy as np

from oscar_mcp.models.unified import UnifiedSession
from oscar_mcp.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


def serialize_waveform(waveform) -> bytes:
    """
    Serialize waveform data to bytes for database storage.

    Stores timestamps and values as float32 numpy arrays.
    No compression - SQLite and filesystem handle that efficiently.

    Args:
        waveform: WaveformData object

    Returns:
        Serialized bytes
    """
    # Convert to numpy arrays if needed
    if isinstance(waveform.timestamps, list):
        timestamps = np.array(waveform.timestamps, dtype=np.float32)
    else:
        timestamps = waveform.timestamps.astype(np.float32)

    if isinstance(waveform.values, list):
        values = np.array(waveform.values, dtype=np.float32)
    else:
        values = waveform.values.astype(np.float32)

    # Stack into 2D array and serialize
    data = np.column_stack([timestamps, values])
    return data.tobytes()


class SessionImporter:
    """Handles importing UnifiedSession objects to database."""

    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize importer.

        Args:
            db_manager: DatabaseManager instance
        """
        self.db = db_manager

    def import_session(self, session: UnifiedSession, force: bool = False) -> bool:
        """
        Import a complete session to database.

        Args:
            session: UnifiedSession to import
            force: If True, re-import existing sessions

        Returns:
            True if imported, False if skipped (already exists)
        """
        # Upsert device
        device_id = self.db.upsert_device(session.device_info)

        # Check if session exists
        if not force and self.db.session_exists(device_id, session.device_session_id):
            logger.debug(f"Session {session.device_session_id} already exists, skipping")
            return False

        # If forcing and session exists, get ID for update
        if force:
            existing_session_id = self.db.get_session_id(device_id, session.device_session_id)
            if existing_session_id:
                logger.info(f"Force re-importing session {session.device_session_id}")
                self._delete_session_data(existing_session_id)

        # Import in transaction
        with self.db.transaction() as conn:
            # Insert session
            session_id = self._insert_session(conn, session, device_id)

            # Import waveforms
            if session.has_waveform_data:
                self._import_waveforms(conn, session_id, session)

            # Import events
            if session.has_event_data:
                self._import_events(conn, session_id, session)

            # Import statistics
            if session.has_statistics:
                self._import_statistics(conn, session_id, session)

            # Import settings
            if session.settings:
                self._import_settings(conn, session_id, session)

        logger.info(f"Imported session {session.device_session_id} from {session.start_time}")
        return True

    def _insert_session(self, conn, session: UnifiedSession, device_id: int) -> int:
        """Insert session record."""
        # Convert data_quality_notes to JSON
        notes_json = json.dumps(session.data_quality_notes) if session.data_quality_notes else None

        cursor = conn.execute(
            """
            INSERT INTO sessions (
                device_id, device_session_id, start_time, end_time,
                duration_seconds, therapy_mode, import_source, parser_version,
                data_quality_notes, has_waveform_data, has_event_data, has_statistics
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                device_id,
                session.device_session_id,
                session.start_time,
                session.end_time,
                session.duration_seconds,
                session.settings.mode.value if session.settings else None,
                session.import_source,
                session.parser_version,
                notes_json,
                session.has_waveform_data,
                session.has_event_data,
                session.has_statistics,
            ),
        )

        return cursor.lastrowid

    def _import_waveforms(self, conn, session_id: int, session: UnifiedSession):
        """Import all waveforms for session."""
        for waveform_type, waveform in session.waveforms.items():
            # Serialize waveform data
            data_blob = serialize_waveform(waveform)

            # Get sample count
            sample_count = (
                len(waveform.values) if isinstance(waveform.values, list) else len(waveform.values)
            )

            # Insert waveform
            conn.execute(
                """
                INSERT INTO waveforms (
                    session_id, waveform_type, sample_rate, unit,
                    min_value, max_value, mean_value,
                    data_blob, sample_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session_id,
                    waveform_type.value,
                    waveform.sample_rate,
                    waveform.unit,
                    waveform.min_value,
                    waveform.max_value,
                    waveform.mean_value,
                    data_blob,
                    sample_count,
                ),
            )

            logger.debug(
                f"Imported waveform {waveform_type.value}: "
                f"{sample_count} samples, "
                f"{len(data_blob) / 1024:.1f} KB"
            )

    def _import_events(self, conn, session_id: int, session: UnifiedSession):
        """Import all respiratory events for session."""
        for event in session.events:
            conn.execute(
                """
                INSERT INTO events (
                    session_id, event_type, start_time, duration_seconds,
                    spo2_drop, peak_flow_limitation
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    session_id,
                    event.event_type.value,
                    event.start_time,
                    event.duration_seconds,
                    event.spo2_drop,
                    event.peak_flow_limitation,
                ),
            )

        logger.debug(f"Imported {len(session.events)} events")

    def _import_statistics(self, conn, session_id: int, session: UnifiedSession):
        """Import session statistics."""
        stats = session.statistics

        conn.execute(
            """
            INSERT INTO statistics (
                session_id,
                obstructive_apneas, central_apneas, mixed_apneas,
                hypopneas, reras, flow_limitations,
                ahi, oai, cai, hi, rei,
                pressure_min, pressure_max, pressure_median, pressure_mean, pressure_95th,
                leak_min, leak_max, leak_median, leak_mean, leak_95th, leak_percentile_70,
                respiratory_rate_min, respiratory_rate_max, respiratory_rate_mean,
                tidal_volume_min, tidal_volume_max, tidal_volume_mean,
                minute_ventilation_min, minute_ventilation_max, minute_ventilation_mean,
                spo2_min, spo2_max, spo2_mean, spo2_time_below_90,
                pulse_min, pulse_max, pulse_mean,
                usage_hours
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
        """,
            (
                session_id,
                stats.obstructive_apneas,
                stats.central_apneas,
                stats.mixed_apneas,
                stats.hypopneas,
                stats.reras,
                stats.flow_limitations,
                stats.ahi,
                stats.oai,
                stats.cai,
                stats.hi,
                stats.rei,
                stats.pressure_min,
                stats.pressure_max,
                stats.pressure_median,
                stats.pressure_mean,
                stats.pressure_95th,
                stats.leak_min,
                stats.leak_max,
                stats.leak_median,
                stats.leak_mean,
                stats.leak_95th,
                stats.leak_percentile_70,
                stats.respiratory_rate_min,
                stats.respiratory_rate_max,
                stats.respiratory_rate_mean,
                stats.tidal_volume_min,
                stats.tidal_volume_max,
                stats.tidal_volume_mean,
                stats.minute_ventilation_min,
                stats.minute_ventilation_max,
                stats.minute_ventilation_mean,
                stats.spo2_min,
                stats.spo2_max,
                stats.spo2_mean,
                stats.spo2_time_below_90,
                stats.pulse_min,
                stats.pulse_max,
                stats.pulse_mean,
                stats.usage_hours,
            ),
        )

        logger.debug("Imported session statistics")

    def _import_settings(self, conn, session_id: int, session: UnifiedSession):
        """Import session settings."""
        settings = session.settings

        if not settings:
            return

        # Store therapy mode and other settings as key-value pairs
        settings_dict = {
            "mode": settings.mode.value,
            "pressure_min": settings.pressure_min,
            "pressure_max": settings.pressure_max,
            "pressure_fixed": settings.pressure_fixed,
            "ipap": settings.ipap,
            "epap": settings.epap,
            "epr_level": settings.epr_level,
            "ramp_time": settings.ramp_time,
            "ramp_start_pressure": settings.ramp_start_pressure,
            "humidity_level": settings.humidity_level,
            "tube_temp": settings.tube_temp,
            "mask_type": settings.mask_type,
        }

        # Add other_settings if present
        if settings.other_settings:
            settings_dict.update(settings.other_settings)

        # Insert all settings
        for key, value in settings_dict.items():
            if value is not None:
                conn.execute(
                    """
                    INSERT INTO settings (session_id, key, value)
                    VALUES (?, ?, ?)
                """,
                    (session_id, key, str(value)),
                )

        logger.debug(f"Imported {len(settings_dict)} settings")

    def _delete_session_data(self, session_id: int):
        """Delete all data for a session (for re-import)."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM waveforms WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM statistics WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM settings WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

        logger.debug(f"Deleted existing session data for session {session_id}")


def import_session(
    db_manager: DatabaseManager, session: UnifiedSession, force: bool = False
) -> bool:
    """
    Convenience function to import a session.

    Args:
        db_manager: DatabaseManager instance
        session: UnifiedSession to import
        force: Force re-import if exists

    Returns:
        True if imported, False if skipped
    """
    importer = SessionImporter(db_manager)
    return importer.import_session(session, force=force)
