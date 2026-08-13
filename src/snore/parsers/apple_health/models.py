"""Canonical record model for Apple Health data from both ingest channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


def apply_noon_split(dt: datetime) -> date:
    """Map a wall-clock datetime to its sleep-night date using a noon boundary.

    Sessions before noon belong to the previous calendar day.  This mirrors
    ``DayManager.get_day_for_session`` / ``DEFAULT_SPLIT_TIME = time(12, 0)``
    so that Apple Health sleep nights share the date convention with CPAP
    ``Day.date``.
    """
    if dt.time() < time(12, 0):
        return dt.date() - timedelta(days=1)
    return dt.date()


@dataclass
class RawHealthRecord:
    """One Apple Health record, normalised from either ingest channel.

    Both the export.xml reader and the HAE JSON reader produce this type.
    The downstream importer layer consumes it without knowing the source format.

    Canonical sleep stage names (``value_text``):
        ``InBed``, ``AsleepUnspecified``, ``Awake``, ``AsleepCore``,
        ``AsleepDeep``, ``AsleepREM``.
    """

    record_type: str
    """Canonical HK identifier, e.g. ``HKCategoryTypeIdentifierSleepAnalysis``."""

    source_name: str

    source_version: str | None

    device_info: str | None

    start_time: datetime
    """Naive local wall-clock datetime."""

    end_time: datetime
    """Naive; point-samples have ``start_time == end_time``."""

    value_text: str | None
    """Canonical stage name for sleep records; ``None`` for quantity records."""

    value_num: float | None
    """Rounded to 4 decimal places at parse; ``None`` for category records."""

    unit: str | None

    utc_offset_seconds: int | None

    night_date: date
    """Noon split applied to ``start_time``."""

    ingest_channel: str
    """``"export_xml"`` or ``"hae_json"``."""
