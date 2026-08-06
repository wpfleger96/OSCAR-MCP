"""
SQLAlchemy ORM models for SNORE database.

Defines the complete database schema including:
- Auth/identity tables (users, auth_identities, invites, oauth_attempts)
- Core CPAP data tables (profiles, devices, sessions, waveforms, events, statistics, settings)
- Medical analysis infrastructure (knowledge base, patterns, analysis results)

Relationship loading policy
---------------------------
All relationships use ``lazy="raise"`` to prevent implicit N+1 queries and to
guarantee that ``MissingGreenlet`` errors cannot occur when these models are
used from async sessions (PR-2).  Every traversal must be explicit: use
``selectinload`` / ``joinedload`` in the calling query or call the relationship
within the open session.

Timestamp classification
------------------------
*Absolute instants* (audit times, import times) use ``UTCDateTime``.  These
values are always written with ``utc_now()`` and must survive a SQLite round-
trip with full timezone information intact.

*Device / session wall-clock columns* (``Session.start_time``, ``Event.start_time``,
``AnalysisResult.timestamp_start`` / ``timestamp_end``,
``DetectedPattern.start_time``) use plain ``DateTime`` without timezone because
CPAP devices record local wall-clock time without a timezone offset.  Storing
them as UTC would invent timezone facts the source data does not contain.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from snore.database.types import UTCDateTime, ValidatedJSONWithDefault


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


def utc_now() -> datetime:
    """Return current UTC timestamp for database defaults."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Auth / identity tables
# ---------------------------------------------------------------------------


class User(Base):
    """Auth identity and account record.  Owns one or more Profiles."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Canonical email: stripped + lower-cased at one normalization point.
    canonical_email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(150))
    # admin | member | demo
    role: Mapped[str] = mapped_column(String(20), default="member")
    # Bumped on password-change/disable/role-change; invalidates all cookies.
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # FK to profiles — set after the first profile is created.
    default_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "profiles.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_default_profile_id_profiles",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, nullable=True, default=dict
    )

    profiles = relationship(
        "Profile",
        back_populates="user",
        foreign_keys="Profile.user_id",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    auth_identities = relationship(
        "AuthIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    invites_created = relationship(
        "Invite",
        back_populates="created_by_user",
        foreign_keys="Invite.created_by",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("length(canonical_email) > 0", name="chk_user_email"),
        CheckConstraint("role IN ('admin','member','demo')", name="chk_user_role"),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.canonical_email}, role={self.role})>"


class AuthIdentity(Base):
    """OAuth identity linked to a User (provider + subject pair)."""

    __tablename__ = "auth_identities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50))  # "google"
    subject: Mapped[str] = mapped_column(String(255))  # provider's user ID
    email: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    user = relationship("User", back_populates="auth_identities", lazy="raise")

    __table_args__ = (
        UniqueConstraint(
            "provider", "subject", name="uq_auth_identity_provider_subject"
        ),
    )

    def __repr__(self) -> str:
        return f"<AuthIdentity(id={self.id}, provider={self.provider}, user_id={self.user_id})>"


class Invite(Base):
    """Invitation to create a SNORE account."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    created_by_user = relationship(
        "User",
        back_populates="invites_created",
        foreign_keys=[created_by],
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin','member','demo')", name="chk_invite_role"),
    )

    def __repr__(self) -> str:
        return f"<Invite(id={self.id}, email={self.email})>"


class OauthAttempt(Base):
    """Server-side OAuth flow state (one-use, browser-bound)."""

    __tablename__ = "oauth_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # "login" | "signup"
    invite_id: Mapped[int | None] = mapped_column(
        ForeignKey("invites.id", ondelete="SET NULL")
    )
    expected_canonical_email: Mapped[str | None] = mapped_column(String(254))
    nonce: Mapped[str | None] = mapped_column(String(128))
    pkce_verifier: Mapped[str | None] = mapped_column(String(128))
    # SHA-256 of the pre-auth browser session cookie value
    browser_session_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    __table_args__ = (
        Index("ix_oauth_attempts_expires_at", "expires_at"),
        CheckConstraint("kind IN ('login','signup')", name="chk_oauth_kind"),
    )

    def __repr__(self) -> str:
        return (
            f"<OauthAttempt(id={self.id}, kind={self.kind}, state={self.state[:8]}...)>"
        )


# ---------------------------------------------------------------------------
# Profile (dataset container, owned by a User)
# ---------------------------------------------------------------------------


class Profile(Base):
    """Dataset container for one user's CPAP data (OSCAR-compatible)."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Human-readable display name (e.g. "My ResMed AirSense 11")
    name: Mapped[str] = mapped_column(String(150))
    # Legacy OSCAR-compatible fields (kept for import compatibility)
    username: Mapped[str | None] = mapped_column(String(100))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, default=dict
    )
    # Deletion tombstone: non-null means profile is being deleted.
    # Tombstoned profiles are invisible to context construction and all queries.
    deleting_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )

    user = relationship(
        "User", back_populates="profiles", foreign_keys=[user_id], lazy="raise"
    )
    devices = relationship(
        "Device",
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_profile_user_name"),
        CheckConstraint("length(name) > 0", name="chk_profile_name"),
    )

    def __repr__(self) -> str:
        return f"<Profile(id={self.id}, user_id={self.user_id}, name={self.name})>"


# ---------------------------------------------------------------------------
# Core CPAP data tables
# ---------------------------------------------------------------------------


class Device(Base):
    """CPAP/BiPAP/Oximeter device, owned by a Profile."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE")
    )
    manufacturer: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    serial_number: Mapped[str] = mapped_column(String)
    firmware_version: Mapped[str | None] = mapped_column(String)
    hardware_version: Mapped[str | None] = mapped_column(String)
    product_code: Mapped[str | None] = mapped_column(String)
    # Absolute audit instants.
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    last_import: Mapped[datetime | None] = mapped_column(UTCDateTime)

    profile = relationship("Profile", back_populates="devices", lazy="raise")
    sessions = relationship(
        "Session",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="raise",
        overlaps="day,sessions",
    )
    days = relationship(
        "Day",
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        # Serial number is unique per profile (not globally).
        UniqueConstraint(
            "profile_id", "serial_number", name="uq_device_profile_serial"
        ),
        CheckConstraint("length(manufacturer) > 0", name="chk_manufacturer"),
        CheckConstraint("length(serial_number) > 0", name="chk_serial"),
    )

    def __repr__(self) -> str:
        return f"<Device(id={self.id}, profile_id={self.profile_id}, manufacturer={self.manufacturer}, model={self.model}, serial={self.serial_number})>"


class Day(Base):
    """Daily aggregated statistics (OSCAR-compatible pre-calculated cache)."""

    __tablename__ = "days"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)

    # Pre-calculated statistics (cached for performance)
    session_count: Mapped[int] = mapped_column(Integer, default=0)
    total_therapy_hours: Mapped[float] = mapped_column(Float, default=0.0)

    obstructive_apneas: Mapped[int] = mapped_column(Integer, default=0)
    central_apneas: Mapped[int] = mapped_column(Integer, default=0)
    hypopneas: Mapped[int] = mapped_column(Integer, default=0)
    reras: Mapped[int] = mapped_column(Integer, default=0)

    ahi: Mapped[float | None] = mapped_column(Float)
    oai: Mapped[float | None] = mapped_column(Float)
    cai: Mapped[float | None] = mapped_column(Float)
    hi: Mapped[float | None] = mapped_column(Float)

    pressure_min: Mapped[float | None] = mapped_column(Float)
    pressure_max: Mapped[float | None] = mapped_column(Float)
    pressure_median: Mapped[float | None] = mapped_column(Float)
    pressure_mean: Mapped[float | None] = mapped_column(Float)
    pressure_95th: Mapped[float | None] = mapped_column(Float)

    epap_min: Mapped[float | None] = mapped_column(Float)
    epap_max: Mapped[float | None] = mapped_column(Float)
    epap_median: Mapped[float | None] = mapped_column(Float)
    epap_mean: Mapped[float | None] = mapped_column(Float)
    epap_95th: Mapped[float | None] = mapped_column(Float)

    leak_min: Mapped[float | None] = mapped_column(Float)
    leak_max: Mapped[float | None] = mapped_column(Float)
    leak_median: Mapped[float | None] = mapped_column(Float)
    leak_mean: Mapped[float | None] = mapped_column(Float)
    leak_95th: Mapped[float | None] = mapped_column(Float)

    spo2_min: Mapped[float | None] = mapped_column(Float)
    spo2_max: Mapped[float | None] = mapped_column(Float)
    spo2_mean: Mapped[float | None] = mapped_column(Float)

    # Absolute audit instants.
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )

    device = relationship("Device", back_populates="days", lazy="raise")
    sessions = relationship(
        "Session", back_populates="day", lazy="raise", overlaps="device,sessions"
    )

    __table_args__ = (
        UniqueConstraint("device_id", "date", name="uq_device_date"),
        # Supports composite FK from Session(day_id, device_id) → Day(id, device_id)
        # so both ownership joins are provably identical.
        UniqueConstraint("id", "device_id", name="uq_day_id_device"),
    )

    def __repr__(self) -> str:
        return f"<Day(id={self.id}, device_id={self.device_id}, date={self.date}, ahi={self.ahi})>"


class Session(Base):
    """Individual therapy session."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    day_id: Mapped[int | None] = mapped_column(Integer)
    device_session_id: Mapped[str] = mapped_column(String)
    # Device wall-clock times: no source timezone, never convert.
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    therapy_mode: Mapped[str | None] = mapped_column(String)
    # Absolute audit instant.
    import_date: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    import_source: Mapped[str | None] = mapped_column(String)
    parser_version: Mapped[str | None] = mapped_column(String)
    data_quality_notes: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, default=dict
    )
    has_waveform_data: Mapped[bool] = mapped_column(Boolean, default=False)
    has_event_data: Mapped[bool] = mapped_column(Boolean, default=False)
    has_statistics: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    device = relationship(
        "Device", back_populates="sessions", lazy="raise", overlaps="day,sessions"
    )
    day = relationship(
        "Day",
        back_populates="sessions",
        foreign_keys="[Session.day_id]",
        lazy="raise",
    )
    waveforms = relationship(
        "Waveform",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    events = relationship(
        "Event",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    statistics = relationship(
        "Statistics",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="raise",
    )
    settings = relationship(
        "Setting",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    analysis_results = relationship(
        "AnalysisResult",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    breaths = relationship(
        "Breath",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
        overlaps="analysis_result",
    )

    __table_args__ = (
        UniqueConstraint("device_id", "device_session_id", name="uq_device_session"),
        CheckConstraint("end_time >= start_time", name="chk_time_range"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="chk_duration"
        ),
        # Composite FK: ensures session.day_id and session.device_id agree on ownership.
        # Day.UNIQUE(id, device_id) makes this constraint enforceable.
        # When day_id IS NULL (session not yet linked to a day), the FK is skipped.
        ForeignKeyConstraint(
            ["day_id", "device_id"],
            ["days.id", "days.device_id"],
            name="fk_session_day_device",
            ondelete="CASCADE",
            use_alter=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<Session(id={self.id}, device_id={self.device_id}, start={self.start_time})>"


class Waveform(Base):
    """Time-series waveform data."""

    __tablename__ = "waveforms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    waveform_type: Mapped[str] = mapped_column(String)
    sample_rate: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    mean_value: Mapped[float | None] = mapped_column(Float)
    data_blob: Mapped[bytes] = mapped_column(LargeBinary)
    sample_count: Mapped[int | None] = mapped_column(Integer)

    session = relationship("Session", back_populates="waveforms", lazy="raise")

    __table_args__ = (
        UniqueConstraint("session_id", "waveform_type", name="uq_session_waveform"),
        CheckConstraint("sample_rate > 0", name="chk_sample_rate"),
    )

    def __repr__(self) -> str:
        return f"<Waveform(id={self.id}, session_id={self.session_id}, type={self.waveform_type})>"


class Event(Base):
    """Respiratory events and flags."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String)
    # Device wall-clock time: no source timezone, never convert.
    start_time: Mapped[datetime] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    spo2_drop: Mapped[float | None] = mapped_column(Float)
    peak_flow_limitation: Mapped[float | None] = mapped_column(Float)

    session = relationship("Session", back_populates="events", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="chk_duration"
        ),
    )

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, session_id={self.session_id}, type={self.event_type}, start={self.start_time})>"


class Statistics(Base):
    """Session statistics and pre-calculated summary data."""

    __tablename__ = "statistics"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )

    obstructive_apneas: Mapped[int] = mapped_column(Integer, default=0)
    central_apneas: Mapped[int] = mapped_column(Integer, default=0)
    mixed_apneas: Mapped[int] = mapped_column(Integer, default=0)
    hypopneas: Mapped[int] = mapped_column(Integer, default=0)
    reras: Mapped[int] = mapped_column(Integer, default=0)
    flow_limitations: Mapped[int] = mapped_column(Integer, default=0)

    ahi: Mapped[float | None] = mapped_column(Float)
    oai: Mapped[float | None] = mapped_column(Float)
    cai: Mapped[float | None] = mapped_column(Float)
    hi: Mapped[float | None] = mapped_column(Float)
    rei: Mapped[float | None] = mapped_column(Float)

    pressure_min: Mapped[float | None] = mapped_column(Float)
    pressure_max: Mapped[float | None] = mapped_column(Float)
    pressure_median: Mapped[float | None] = mapped_column(Float)
    pressure_mean: Mapped[float | None] = mapped_column(Float)
    pressure_95th: Mapped[float | None] = mapped_column(Float)

    epap_min: Mapped[float | None] = mapped_column(Float)
    epap_max: Mapped[float | None] = mapped_column(Float)
    epap_median: Mapped[float | None] = mapped_column(Float)
    epap_mean: Mapped[float | None] = mapped_column(Float)
    epap_95th: Mapped[float | None] = mapped_column(Float)

    ipap_median: Mapped[float | None] = mapped_column(Float)
    ipap_95th: Mapped[float | None] = mapped_column(Float)
    ipap_max: Mapped[float | None] = mapped_column(Float)

    leak_min: Mapped[float | None] = mapped_column(Float)
    leak_max: Mapped[float | None] = mapped_column(Float)
    leak_median: Mapped[float | None] = mapped_column(Float)
    leak_mean: Mapped[float | None] = mapped_column(Float)
    leak_95th: Mapped[float | None] = mapped_column(Float)
    leak_percentile_70: Mapped[float | None] = mapped_column(Float)

    respiratory_rate_min: Mapped[float | None] = mapped_column(Float)
    respiratory_rate_max: Mapped[float | None] = mapped_column(Float)
    respiratory_rate_mean: Mapped[float | None] = mapped_column(Float)

    tidal_volume_min: Mapped[float | None] = mapped_column(Float)
    tidal_volume_max: Mapped[float | None] = mapped_column(Float)
    tidal_volume_mean: Mapped[float | None] = mapped_column(Float)

    minute_ventilation_min: Mapped[float | None] = mapped_column(Float)
    minute_ventilation_max: Mapped[float | None] = mapped_column(Float)
    minute_ventilation_mean: Mapped[float | None] = mapped_column(Float)

    spo2_min: Mapped[float | None] = mapped_column(Float)
    spo2_max: Mapped[float | None] = mapped_column(Float)
    spo2_mean: Mapped[float | None] = mapped_column(Float)
    spo2_time_below_90: Mapped[int | None] = mapped_column(Integer)
    pulse_min: Mapped[float | None] = mapped_column(Float)
    pulse_max: Mapped[float | None] = mapped_column(Float)
    pulse_mean: Mapped[float | None] = mapped_column(Float)

    usage_hours: Mapped[float | None] = mapped_column(Float)

    session = relationship("Session", back_populates="statistics", lazy="raise")

    def __repr__(self) -> str:
        return f"<Statistics(session_id={self.session_id}, ahi={self.ahi})>"


class Setting(Base):
    """Device configuration settings."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str | None] = mapped_column(String)

    session = relationship("Session", back_populates="settings", lazy="raise")

    __table_args__ = (UniqueConstraint("session_id", "key", name="uq_session_key"),)

    def __repr__(self) -> str:
        return f"<Setting(id={self.id}, session_id={self.session_id}, key={self.key})>"


class AnalysisResult(Base):
    """Track programmatic analysis results for sessions."""

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    # Device/session wall-clock windows: no source timezone, never convert.
    timestamp_start: Mapped[datetime] = mapped_column(DateTime)
    timestamp_end: Mapped[datetime] = mapped_column(DateTime)
    programmatic_result_json: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, default=dict
    )
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    engine_versions_json: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, default=dict
    )
    # Absolute audit instant — used for latest-result selection.
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    session = relationship("Session", back_populates="analysis_results", lazy="raise")
    detected_patterns = relationship(
        "DetectedPattern",
        back_populates="analysis",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    breaths = relationship(
        "Breath",
        back_populates="analysis_result",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<AnalysisResult(id={self.id}, session_id={self.session_id})>"


class Breath(Base):
    """Immutable per-breath metrics persisted at analysis time.

    Breaths are immutable children of a specific AnalysisResult (analysis run).
    Re-analysis appends a new AnalysisResult + new Breath children; prior runs
    and their breaths are never deleted except via the explicit deletion API
    (cascade handles children).

    **No Alembic migration** (ruling #4): this model ships model-only; fresh DBs
    get the schema via Base.metadata.create_all; pre-existing DBs receive a
    capability-honest error → drop + reimport.

    Denormalised session_id is permitted for query efficiency; uniqueness is on
    (analysis_result_id, breath_number).
    """

    __tablename__ = "breaths"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    analysis_result_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_results.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised for efficient per-session queries.
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    breath_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timing (session-relative offsets in seconds)
    start_offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    inspiration_time_s: Mapped[float | None] = mapped_column(Float)  # Ti
    expiration_time_s: Mapped[float | None] = mapped_column(Float)  # Te
    total_time_s: Mapped[float | None] = mapped_column(Float)  # Ttot
    i_e_ratio: Mapped[float | None] = mapped_column(Float)
    duty_cycle: Mapped[float | None] = mapped_column(Float)  # Ti/Ttot

    # Amplitude
    peak_flow_lpm: Mapped[float | None] = mapped_column(Float)  # peak inspiratory
    peak_exp_flow_lpm: Mapped[float | None] = mapped_column(Float)  # peak expiratory
    tidal_volume_ml: Mapped[float | None] = mapped_column(Float)
    respiratory_rate_rolling: Mapped[float | None] = mapped_column(
        Float
    )  # RR from rolling window

    # Flow shape — time-above-80%-peak (existing ShapeFeatures.flatness_index)
    flatness_index: Mapped[float | None] = mapped_column(Float)
    # NEW: mid-inspiratory flattening = mid-insp flow ÷ peak (different metric)
    mid_insp_flattening: Mapped[float | None] = mapped_column(Float)

    # Flow classification (FlowLimitationClassifier 7-class taxonomy)
    flow_class: Mapped[int | None] = mapped_column(Integer)
    flow_confidence: Mapped[float | None] = mapped_column(Float)

    # Recovery-breath flag (from primary mode's RERA detector)
    is_recovery_breath: Mapped[bool | None] = mapped_column(Boolean)

    # Trigger/cycle inference (experimental, heuristic — non-ResMed → null)
    inferred_trigger_type: Mapped[str | None] = mapped_column(String)
    trigger_confidence: Mapped[float | None] = mapped_column(Float)
    inferred_cycle_type: Mapped[str | None] = mapped_column(String)
    cycle_confidence: Mapped[float | None] = mapped_column(Float)
    # Per-device applicability: null + reason for unvalidated devices
    trigger_cycle_applicable: Mapped[bool | None] = mapped_column(Boolean)
    trigger_cycle_reason: Mapped[str | None] = mapped_column(String)

    # Quality flags (all nullable + reason; see plan step 3)
    leak_valid: Mapped[bool | None] = mapped_column(Boolean)
    leak_valid_reason: Mapped[str | None] = mapped_column(String)
    ramp_active: Mapped[bool | None] = mapped_column(Boolean)
    ramp_active_reason: Mapped[str | None] = mapped_column(
        String, default="not_available"
    )
    mask_off: Mapped[bool | None] = mapped_column(Boolean)
    mask_off_reason: Mapped[str | None] = mapped_column(String, default="not_available")

    analysis_result = relationship(
        "AnalysisResult",
        back_populates="breaths",
        lazy="raise",
    )
    session = relationship(
        "Session",
        back_populates="breaths",
        lazy="raise",
        overlaps="analysis_result",
    )

    __table_args__ = (
        UniqueConstraint(
            "analysis_result_id",
            "breath_number",
            name="uq_breath_run_number",
        ),
        Index("ix_breath_analysis_start", "analysis_result_id", "start_offset_s"),
        Index("ix_breath_session_id", "session_id"),
        CheckConstraint(
            "flow_class IS NULL OR (flow_class >= 1 AND flow_class <= 7)",
            name="chk_breath_flow_class",
        ),
        CheckConstraint(
            "flow_confidence IS NULL OR (flow_confidence >= 0 AND flow_confidence <= 1)",
            name="chk_breath_flow_confidence",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Breath(id={self.id}, analysis_result_id={self.analysis_result_id}, "
            f"breath_number={self.breath_number})>"
        )


class DetectedPattern(Base):
    """
    Individual pattern detections from analysis.

    Pattern definitions are stored in code (snore.knowledge.patterns), not database.
    This table only stores runtime detections with references to pattern IDs.
    """

    __tablename__ = "detected_patterns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    analysis_result_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_results.id", ondelete="CASCADE")
    )
    pattern_id: Mapped[str] = mapped_column(String(100))
    # Device/session wall-clock time: no source timezone, never convert.
    start_time: Mapped[datetime] = mapped_column(DateTime)
    duration: Mapped[float | None] = mapped_column(Float)  # seconds
    confidence: Mapped[float] = mapped_column(Float)
    detected_by: Mapped[str] = mapped_column(String(20))  # programmatic
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        ValidatedJSONWithDefault, default=dict
    )
    notes: Mapped[str | None] = mapped_column(Text)

    analysis = relationship(
        "AnalysisResult", back_populates="detected_patterns", lazy="raise"
    )

    def __repr__(self) -> str:
        return f"<DetectedPattern(id={self.id}, pattern={self.pattern_id}, confidence={self.confidence})>"
