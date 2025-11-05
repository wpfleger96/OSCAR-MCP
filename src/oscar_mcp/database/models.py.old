"""SQLAlchemy ORM models for OSCAR data storage."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from oscar_mcp.database.session import Base


class Profile(Base):
    """User profile containing all therapy data."""

    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # User information
    first_name = Column(String(100))
    last_name = Column(String(100))
    date_of_birth = Column(Date)
    height_cm = Column(Float)
    notes = Column(Text)

    # Relationships
    machines = relationship("Machine", back_populates="profile", cascade="all, delete-orphan")
    days = relationship("Day", back_populates="profile", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Profile(id={self.id}, name='{self.name}')>"


class Machine(Base):
    """CPAP/BiPAP/Oximeter device."""

    __tablename__ = "machines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)

    # Machine identification
    machine_id = Column(String(100), nullable=False, unique=True)  # OSCAR's unique ID
    serial_number = Column(String(100))
    brand = Column(String(100))
    model = Column(String(100))
    machine_type = Column(String(50), nullable=False)  # CPAP, BiPAP, Oximeter, etc.

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_import = Column(DateTime)

    # Relationships
    profile = relationship("Profile", back_populates="machines")
    sessions = relationship("Session", back_populates="machine", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Machine(id={self.id}, brand='{self.brand}', model='{self.model}', serial='{self.serial_number}')>"


class Day(Base):
    """Aggregated data for a single calendar day."""

    __tablename__ = "days"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    date = Column(Date, nullable=False)

    # Summary statistics (cached)
    total_therapy_hours = Column(Float)
    ahi = Column(Float)  # Apnea-Hypopnea Index
    rdi = Column(Float)  # Respiratory Disturbance Index

    # Event counts
    obstructive_apneas = Column(Integer, default=0)
    hypopneas = Column(Integer, default=0)
    central_apneas = Column(Integer, default=0)
    reras = Column(Integer, default=0)
    flow_limitations = Column(Integer, default=0)

    # Pressure statistics
    pressure_median = Column(Float)
    pressure_95th = Column(Float)
    pressure_max = Column(Float)

    # Leak statistics
    leak_median = Column(Float)
    leak_95th = Column(Float)
    leak_max = Column(Float)

    # SpO2 statistics (if available)
    spo2_avg = Column(Float)
    spo2_min = Column(Float)
    spo2_median = Column(Float)
    pulse_avg = Column(Float)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    profile = relationship("Profile", back_populates="days")
    sessions = relationship("Session", back_populates="day", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Day(id={self.id}, date='{self.date}', ahi={self.ahi})>"


class Session(Base):
    """Individual therapy session."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, ForeignKey("machines.id"), nullable=False)
    day_id = Column(Integer, ForeignKey("days.id"), nullable=False)

    # Session identification
    session_id = Column(String(100), nullable=False, unique=True)  # OSCAR's unique session ID

    # Timing (stored as Unix timestamps in milliseconds)
    start_time = Column(BigInteger, nullable=False)  # ms since epoch
    end_time = Column(BigInteger, nullable=False)  # ms since epoch
    duration_seconds = Column(Integer, nullable=False)

    # Summary statistics
    ahi = Column(Float)
    obstructive_apneas = Column(Integer, default=0)
    hypopneas = Column(Integer, default=0)
    central_apneas = Column(Integer, default=0)
    reras = Column(Integer, default=0)

    # Pressure
    pressure_min = Column(Float)
    pressure_max = Column(Float)
    pressure_median = Column(Float)
    pressure_95th = Column(Float)

    # Leak
    leak_median = Column(Float)
    leak_95th = Column(Float)
    leak_max = Column(Float)

    # Respiratory
    resp_rate_avg = Column(Float)
    tidal_volume_avg = Column(Float)
    minute_vent_avg = Column(Float)

    # SpO2 (if available)
    spo2_avg = Column(Float)
    spo2_min = Column(Float)
    pulse_avg = Column(Float)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    machine = relationship("Machine", back_populates="sessions")
    day = relationship("Day", back_populates="sessions")
    event_data = relationship("EventData", back_populates="session", cascade="all, delete-orphan")
    settings = relationship(
        "SessionSetting", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Session(id={self.id}, session_id='{self.session_id}', duration={self.duration_seconds}s)>"


class EventData(Base):
    """Time-series event/waveform data for a session."""

    __tablename__ = "event_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)

    # Channel identification
    channel_id = Column(Integer, nullable=False)  # From OSCAR channel IDs
    channel_type = Column(String(50), nullable=False)  # WAVEFORM, FLAG, etc.

    # Data storage (compressed binary format)
    data_blob = Column(LargeBinary, nullable=False)  # Gzip compressed data
    time_blob = Column(LargeBinary)  # Delta-encoded timestamps for events

    # Metadata for reconstruction
    sample_rate = Column(Float)  # Samples per second (for waveforms)
    gain = Column(Float, default=1.0)
    offset = Column(Float, default=0.0)
    min_value = Column(Float)
    max_value = Column(Float)
    data_count = Column(Integer, nullable=False)  # Number of data points

    # Relationships
    session = relationship("Session", back_populates="event_data")

    def __repr__(self):
        return f"<EventData(id={self.id}, channel_id={self.channel_id}, count={self.data_count})>"


class SessionSetting(Base):
    """Device settings for a therapy session."""

    __tablename__ = "session_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)

    # Setting identification
    setting_name = Column(String(100), nullable=False)
    setting_value = Column(String(255), nullable=False)

    # Relationships
    session = relationship("Session", back_populates="settings")

    def __repr__(self):
        return f"<SessionSetting(id={self.id}, name='{self.setting_name}', value='{self.setting_value}')>"


class Statistics(Base):
    """Pre-calculated statistics for various time periods."""

    __tablename__ = "statistics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)

    # Time period
    period_type = Column(String(20), nullable=False)  # daily, weekly, monthly, yearly
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Compliance
    days_used = Column(Integer)
    avg_hours_per_day = Column(Float)
    compliance_rate = Column(Float)  # Percentage of days with >4 hours

    # Therapy metrics
    avg_ahi = Column(Float)
    median_ahi = Column(Float)
    avg_pressure = Column(Float)
    avg_leak = Column(Float)

    # SpO2 metrics
    avg_spo2 = Column(Float)
    min_spo2 = Column(Float)

    # Metadata
    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Statistics(id={self.id}, period={self.period_type}, start={self.period_start})>"
