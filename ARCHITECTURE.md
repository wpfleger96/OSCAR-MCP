# SNORE Architecture

Technical documentation for the SNORE system architecture, components, and design decisions.

---

## System Overview

### Modular, Parser-Agnostic Platform

```
┌─────────────────────────────────────────────┐
│        Vue 3 Frontend (ui/)                │
│  Dashboard, Session Explorer, Waveforms     │
│  PrimeVue components + uPlot charts         │
├─────────────────────────────────────────────┤
│        FastAPI REST API (api/)              │
│  8 routers, 24 endpoints, LTTB             │
│  OpenAPI docs at /docs                      │
├─────────────────────────────────────────────┤
│        Service Layer (services/)            │
│  12 services: business logic between        │
│  CLI/API and database                       │
├─────────────────────────────────────────────┤
│        Analysis Layer (Parser Agnostic)     │
│  Breath segmentation, event detection,      │
│  flow limitation, pulse change detection    │
├─────────────────────────────────────────────┤
│         SQLite Database ✅                  │
│  Universal schema, direct BLOB storage      │
│  Alembic migrations, 10 tables              │
├─────────────────────────────────────────────┤
│         CLI Import Tool ✅                  │
│  snore import (auto-detection)              │
│  Raw backup, parallel parsing               │
├─────────────────────────────────────────────┤
│         Unified Data Model                  │
│  UnifiedSession, WaveformData, etc.         │
│  All parsers output to this format          │
├─────────────────────────────────────────────┤
│         Parser Registry & Detection         │
│  Auto-detects device from file structure    │
│  Confidence-based selection                 │
├─────────────────────────────────────────────┤
│           Device Parser Plugins             │
│ ┌──────────┬──────────┬──────────┬────────┐│
│ │ ResMed   │ OSCAR    │ Philips  │ Future ││
│ │ EDF+     │ Binary   │ (TODO)   │        ││
│ │   ✅     │  ✅      │          │        ││
│ └──────────┴──────────┴──────────┴────────┘│
└─────────────────────────────────────────────┘
```

### Key Design Principles

1. **Separation of Concerns**: Analysis layer never knows about parser formats
2. **Extensibility**: Add new device support without touching existing code
3. **Single Source of Truth**: SQLite database stores unified format
4. **Auto-Detection**: Users just point to data, it "just works"
5. **Testability**: Each parser tested independently with real data

---

## Core Components

### Unified Data Model

**File:** `src/snore/parsers/unified.py`

All parsers convert to these universal Pydantic structures:

**UnifiedSession**
- Device-agnostic session container
- Start/end times, duration, mode
- Waveforms dict (by type)
- Events list
- Statistics
- Quality notes

**WaveformData**
- Time-series data (Flow, Pressure, Leak, SpO2, Pulse)
- Timestamps as numpy array (seconds offset from session start)
- Values as numpy float32 array
- Sample rate, unit, min/max/mean statistics

**RespiratoryEvent**
- Event types: OA, CA, H, UA, RERA, FL, etc.
- Start time, duration
- Optional annotations (from EVE files)

**DeviceInfo**
- Manufacturer, model, serial number
- Firmware version
- Metadata

**SessionStatistics**
- Event counts (OA, CA, H, etc.)
- Indices (AHI, OAI, CAI, HI, REI)
- Pressure stats (min, max, median, p95)
- Leak stats
- Respiratory stats
- SpO2 stats (min, max, mean, time below 90%)
- Pulse stats

### Parser Infrastructure

**File:** `src/snore/parsers/base.py`

**DeviceParser (Abstract Base Class)**
```python
class DeviceParser(ABC):
    @abstractmethod
    def detect(self, path: Path) -> ParserDetectionResult:
        """Check if this parser can handle the data"""

    @abstractmethod
    def get_device_info(self, path: Path) -> DeviceInfo:
        """Extract device metadata"""

    @abstractmethod
    def parse_sessions(self, path: Path, ...) -> Iterator[UnifiedSession]:
        """Parse all sessions to unified format"""

    @abstractmethod
    def get_metadata(self) -> ParserMetadata:
        """Parser identification and capabilities"""
```

**Adding New Parser**:
```python
# File: src/snore/parsers/philips.py
class PhilipsParser(DeviceParser):
    def detect(self, path):
        return (path / "PXXXXXX").exists()

    def get_device_info(self, path):
        return DeviceInfo(manufacturer="Philips", ...)

    def parse_sessions(self, path):
        # Convert Philips format → UnifiedSession
        yield unified_session

    def get_metadata(self):
        return ParserMetadata(parser_id="philips", ...)

# Auto-register
parser_registry.register(PhilipsParser())
```

### Parser Registry

**File:** `src/snore/parsers/registry.py`

- Auto-detects device type from file structure
- Confidence-based parser selection
- Manufacturer hints (optional)
- Global singleton: `parser_registry`

```python
# Usage
parser = parser_registry.detect_parser(path)
for session in parser.parse_sessions(path):
    # session is UnifiedSession
    print(f"{session.start_time}: {session.duration_hours}h")
```

### EDF+ Reader Library

**File:** `src/snore/parsers/formats/edf.py`

Generic EDF/EDF+ file reader for medical devices:
- Signal extraction with proper unit conversion
- Annotation parsing (for events)
- Header information extraction
- **Reusable** by any EDF-based parser

---

## Analysis System

### Overview

The analysis system performs programmatic respiratory event detection on imported CPAP sessions using configurable detection modes.

**Architecture:**
```
analysis/
├── service.py          # Orchestration
├── types.py            # AnalysisResult, AnalysisEvent
├── calculations.py     # Metric calculations
├── rx_tracker.py       # Prescription change tracking
├── utils.py            # Analysis utilities
├── data/
│   └── waveform_loader.py  # Database waveform loading
├── shared/             # Core algorithms
│   ├── breath_segmenter.py
│   ├── feature_extractors.py
│   ├── flow_limitation.py
│   ├── pattern_detector.py
│   ├── pulse_detector.py       # Pulse change detection (NEW)
│   └── types.py
└── modes/
    ├── config.py       # AASM_CONFIG, AASM_RELAXED_CONFIG, RESMED_CONFIG
    ├── detector.py
    └── types.py
```

### Detection Modes

**Config-Based Strategy Pattern:**
- Single `EventDetector` class configurable via `DetectionModeConfig`
- No class hierarchy - behavior controlled by configuration
- Easily extensible by adding new configs

**Available Modes:**

1. **AASM Mode** (default)
   - AASM Scoring Manual v2.6 compliant
   - Time-based baseline (120 seconds, 2 minutes)
   - 90% validation threshold
   - Strict apnea detection (≥90% flow reduction)
   - Hypopnea detection (30% flow + 3% SpO2 desaturation)
   - RERA detection enabled

2. **AASM Relaxed Mode**
   - AASM-based with relaxed thresholds
   - Breath-based baseline (30 breaths)
   - 85% validation threshold
   - Hypopnea detection (30% flow + 3% SpO2 desaturation)
   - RERA detection enabled
   - Better for matching machine-detected events

3. **ResMed Mode**
   - Approximates ResMed machine detection logic (gap + low-flow approach)
   - Time-based baseline (120 seconds, 2 minutes)
   - 50% apnea threshold (vs 90% AASM) — detects flow gaps, not near-complete cessation
   - Flow-only hypopnea detection (20% reduction, no SpO2 required)
   - RERA detection enabled
   - Designed to match ResMed AirSense/AirCurve event counts

**Configuration Parameters:**
```python
DetectionModeConfig(
    name="mode_name",
    baseline_method=BaselineMethod.TIME | BaselineMethod.BREATH,
    baseline_window=120.0,  # seconds or breath count
    apnea_threshold=0.90,   # 90% flow reduction
    apnea_validation_threshold=0.90,
    hypopnea_min_threshold=0.30,
    hypopnea_max_threshold=0.89,
    min_event_duration=10.0,
    merge_gap=3.0,
    metric="amplitude",
    hypopnea_mode=HypopneaMode.AASM_3PCT | AASM_4PCT | FLOW_ONLY | DISABLED,
    hypopnea_flow_only_fallback=True,  # Fallback if no SpO2 data
    rera_detection_enabled=True  # Detect RERA-like events
)
```

**Hypopnea Detection Modes:**
- `AASM_3PCT` - 30% flow + 3% SpO2 drop (AASM recommended)
- `AASM_4PCT` - 30% flow + 4% SpO2 drop (CMS/Medicare)
- `FLOW_ONLY` - Flow reduction, no SpO2 required (threshold config-controlled; 20% in ResMed mode)
- `DISABLED` - Skip hypopnea detection

### Analysis Pipeline

```
1. Load waveform data (timestamps, flow values)
   ↓
2. BreathSegmenter.segment_breaths()
   → Identifies individual breaths from flow signal
   ↓
3. WaveformFeatureExtractor (per breath)
   → Extracts shape features, spectral features, waveform features
   ↓
4. FlowLimitationClassifier.analyze_session()
   → Classifies flow limitation severity
   ↓
5. ComplexPatternDetector
   → Detects CSR (Cheyne-Stokes Respiration)
   → Detects periodic breathing
   ↓
5.5. PulseChangeDetector
   → Detects pulse rate changes ≥5 BPM within 8-second sliding windows
   → Outputs pulse_change_count and pulse_change_index (per hour)
   ↓
6. EventDetector.detect_events() (per mode)
   → Detects apneas (obstructive, central, mixed, unspecified) with confidence levels
   → Detects hypopneas (mode-dependent: SpO2-based or flow-only)
   → Detects RERAs (Respiratory Effort-Related Arousals)
   → Calculates AHI, RDI (includes RERAs)
   ↓
7. AnalysisResult (stored in database)
   → mode_results: {mode_name: ModeResult}
   → flow_analysis, csr_detection, periodic_breathing
```

### Type System

**All analysis types use Pydantic models:**
- Validation at construction time
- Automatic JSON serialization via `model_dump()`
- No manual `to_dict()`/`from_dict()` methods needed

**Key Types:**
- `BreathMetrics` - Individual breath measurements (Pydantic)
- `ApneaEvent`, `HypopneaEvent`, `RERAEvent` - Detected events (Pydantic)
- `ModeResult` - Per-mode detection results (Pydantic)
- `AnalysisResult` - Complete analysis output (Pydantic)
- `DetectionModeConfig` - Mode configuration (frozen Pydantic)

---

## REST API

### Overview

FastAPI application serving the same data as the CLI through HTTP endpoints. Launched via `snore serve`.

**Application:** `src/snore/api/app.py`

**Routers (8):**
| Router | Prefix | Endpoints |
|--------|--------|-----------|
| sessions | `/sessions` | List, detail, enable/disable, delete |
| waveforms | `/waveforms` | List types, get data (LTTB downsampling) |
| events | `/events` | List, match machine vs programmatic |
| analysis | `/analysis` | List status, get result, run, delete |
| stats | `/stats` | Summary, periods, trends, records |
| devices | `/devices` | List |
| days | `/days` | List, detail |
| rx | `/rx` | History, current, compare |

**Key patterns:**
- Dependency injection: `db: Session = Depends(get_db)` for database sessions
- Caller-controlled transactions: `get_db()` handles commit/rollback
- Domain exceptions: `NotFoundError` → 404, genuine `ValueError` → 500
- LTTB downsampling: 720k-point waveforms served in <100ms via `max_points` param
- CORS: Configured for Vue dev server (`localhost:5173`)
- OpenAPI: Auto-generated docs at `/docs`
- Auth/rate-limit middleware (`api/middleware.py`): no-op stubs — designed for production swap-in

---

## Service Layer

12 service modules in `src/snore/services/` form the business logic layer between CLI/API and database:

| Service | Responsibility |
|---------|---------------|
| AnalysisFacade | Analysis orchestration and result retrieval |
| BackupService | Raw SD card file backup to `~/.snore/raw/` |
| DatabaseService | Database operations (stats, vacuum, init) |
| DayService | Day aggregation and lookup |
| DeviceService | Device management |
| EventService | Event queries and matching |
| ExportService | Data export (CSV, JSON) |
| lttb (module) | Largest-Triangle-Three-Buckets downsampling via `lttb_downsample()` |
| RxService | Prescription/therapy settings tracking |
| SessionService | Session CRUD and filtering |
| StatsService | Statistics calculations and summaries |
| WaveformService | Waveform data access and formatting |

**Pattern:** Constructor injection with SQLAlchemy session, typed Pydantic returns via `services/schemas.py`.

---

## Database Schema

### Tables

**profiles**
```sql
id, username (UNIQUE), first_name, last_name, date_of_birth, height_cm,
settings (JSON), created_at, updated_at
```

**devices**
```sql
id, manufacturer, model, serial_number, firmware_version,
hardware_version, product_code, first_seen, last_import
UNIQUE(serial_number)
```

**days**
```sql
id, device_id (FK devices), date,
session_count, total_therapy_hours,
obstructive_apneas, central_apneas, hypopneas, reras,
ahi, oai, cai, hi,
pressure_min/max/median/mean/95th, epap_min/max/median/mean/95th,
leak_min/max/median/mean/95th, spo2_min/max/mean,
created_at, updated_at
UNIQUE(device_id, date)
```

**sessions**
```sql
id, device_id (FK devices), day_id (FK days),
device_session_id, start_time, end_time, duration_seconds,
therapy_mode, import_date, import_source, parser_version,
data_quality_notes (JSON),
has_waveform_data, has_event_data, has_statistics, enabled
UNIQUE(device_id, device_session_id)
```

**waveforms**
```sql
id, session_id, waveform_type, sample_rate, unit,
min_value, max_value, mean_value, data_blob, sample_count
UNIQUE(session_id, waveform_type)
```
- `data_blob`: Numpy array as bytes (timestamps and values stacked)
- **No compression** - SQLite/filesystem handles that efficiently
- Simplified from original design for performance

**events**
```sql
id, session_id (FK sessions), event_type, start_time, duration_seconds,
spo2_drop, peak_flow_limitation
```

**statistics**
```sql
session_id (PK, FK sessions), [45+ metric columns]
AHI, OAI, CAI, HI, REI, pressure stats, leak stats, SpO2 stats, etc.
```

**settings**
```sql
id, session_id (FK sessions), key, value
UNIQUE(session_id, key)
```
Key-value pairs for extensibility across device types

**analysis_results**
```sql
id, session_id (FK sessions),
timestamp_start, timestamp_end,
programmatic_result_json (JSON), processing_time_ms,
engine_versions_json (JSON), created_at
```
Stores programmatic analysis results (detection mode, events, AHI/RDI, metadata)

**detected_patterns**
```sql
id, analysis_result_id (FK analysis_results),
pattern_id, start_time, duration, confidence,
detected_by, metrics_json (JSON), notes
```

---

## Data Flow

### Current ResMed Flow (Working)
```
ResMed SD Card
    ↓
DATALOG/YYYY/*.edf files
    ↓
ResMed EDF+ Parser (src/snore/parsers/resmed_edf.py)
    ↓
UnifiedSession objects
    ↓
Session Importer (src/snore/database/importers.py)
    ↓
SQLite Database (~/.snore/snore.db)
    ↓
Analysis Tools
    ↓
CLI/Reports
```

### Other Devices Flow (Working)
```
Any CPAP Device
    ↓
OSCAR Desktop App
    ↓
.000/.001 binary files
    ↓
OSCAR Binary Parser ✅
    ↓
UnifiedSession objects
    ↓
[Same pipeline as ResMed]
```

### API Data Flow
```
SQLite Database
    ↓
Service Layer (services/)
    ↓
FastAPI Routers (api/routers/)
    ↓
JSON Response → Vue Frontend (ui/)
```

---

## File Locations

- **Database:** `~/.snore/snore.db`
- **ResMed data:** `DATALOG/YYYY/*.edf` on SD card or `~/Downloads/OSCAR/Profiles/<Profile>/<Device>/Backup/`
- **OSCAR cache:** `~/Downloads/OSCAR/Profiles/<Profile>/<Device>/Summaries/*.000` and `Events/*.001`

---

## Dependencies

**Core:** Python 3.13+, pydantic, sqlalchemy, alembic, click, numpy, pyedflib, scipy, mne, python-dateutil, pytz, requests

**API:** fastapi, uvicorn, httpx

**CLI:** rich, plotext, pint, jinja2, packaging

**Development:** pytest, pytest-cov, ruff, mypy

**All managed via** `pyproject.toml` with uv

---

## Device Support

**ResMed Devices (Direct Import)** ✅
- AirSense 10/11, AirCurve 10/11, S9 series
- Native EDF+ format → Direct import from SD card
- ~40% of CPAP market

**All Other Manufacturers (via OSCAR)** ✅
- Philips, Fisher & Paykel, Löwenstein, Weinmann, DeVilbiss, BMC, and 12+ others
- Import via OSCAR desktop app → Binary cache files (.000/.001)
- OSCAR binary parser fully implemented
- 100% device coverage (all 18 OSCAR-supported manufacturers)
