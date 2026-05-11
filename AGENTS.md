# AGENTS.md

SNORE (Sleep eNvironment Observation & Respiratory Evaluation) is a CLI tool for analyzing CPAP/APAP therapy data with commands for importing, querying, and analyzing sleep therapy sessions.

## Quick Commands

```bash
# Development workflow
just                # Quick check: sync, type-check, lint-check, format-check
just test           # Run all tests
just check-all      # Full quality check + tests
just check          # Full quality check (Python + UI)
just ci             # CI workflow (same as check + test)
just pre-commit     # Pre-commit: sync, type-check, lint, format (Python + UI)
just lint           # Ruff lint with auto-fix
just format         # Ruff format
just docs           # Generate CLI documentation
just sync           # Install dependencies (uv sync)
just dev-api        # Start REST API dev server (with reload)
just dev-ui         # Start Vue UI dev server
just ui-install     # Install UI npm dependencies
just ui-build       # Build UI for production

# CLI (local development - always use `uv run snore`)
uv run snore import <path>                     # Import device data
uv run snore session list                      # List sessions
uv run snore session enable/disable <id>       # Toggle session inclusion
uv run snore analysis run --session-id <id>    # Analyze session
uv run snore analysis list                     # List sessions with analysis status
uv run snore analysis show --date YYYY-MM-DD   # Show analysis results
uv run snore waveform show --date YYYY-MM-DD --time HH:MM:SS  # Visualize waveform
uv run snore waveform compare --session-id <id> --mode aasm   # Compare detection
uv run snore stats                             # Therapy statistics summary
uv run snore stats --period week --trend       # Period breakdown with trends
uv run snore validate --from YYYY-MM-DD --to YYYY-MM-DD  # Batch validation
uv run snore export raw --from YYYY-MM-DD      # Raw file backup
uv run snore export csv --from YYYY-MM-DD      # CSV export
uv run snore export json --from YYYY-MM-DD --to YYYY-MM-DD  # JSON export
uv run snore serve                             # Start REST API (localhost:8000)
uv run snore rx history                        # Prescription settings history
uv run snore rx current                        # Current prescription settings
uv run snore rx compare                        # Compare prescription periods
uv run snore db drop                           # Drop database (with confirmation)
uv run snore db init                           # Initialize database
uv run snore setup --github                    # Install as uv tool from GitHub
uv run snore upgrade                           # Upgrade to latest version
uv run snore --version                         # Show version and check for updates
uv run snore completions install               # Install shell completions
uv run snore logs show/clear/path              # Log management
```

## Project Structure

```
src/snore/
├── cli.py              # CLI commands (Click)
├── constants.py        # Channel IDs, mappings, flow limitation classes
├── completions.py      # Shell completion generation and installation
├── exceptions.py       # Domain exceptions (NotFoundError)
├── logging_config.py   # Logging configuration with rotation
├── types.py            # Shared type definitions
├── analysis/           # Analysis algorithms
│   ├── calculations.py # Metric calculations
│   ├── rx_tracker.py   # Prescription change tracking
│   ├── utils.py        # Analysis utilities
│   ├── service.py      # AnalysisService orchestrator
│   ├── types.py        # AnalysisResult, AnalysisEvent
│   ├── data/
│   │   └── waveform_loader.py  # Database waveform loading
│   ├── shared/         # Core algorithms
│   │   ├── breath_segmenter.py     # Breath segmentation
│   │   ├── feature_extractors.py   # Waveform feature extraction
│   │   ├── flow_limitation.py      # Flow limitation classification
│   │   ├── pattern_detector.py     # CSR, periodic breathing detection
│   │   ├── pulse_detector.py       # Pulse change detection
│   │   └── types.py                # BreathMetrics, ApneaEvent, etc.
│   └── modes/          # Detection modes
│       ├── config.py   # AASM_CONFIG, AASM_RELAXED_CONFIG, RESMED_CONFIG
│       ├── detector.py # EventDetector (configurable)
│       └── types.py    # ModeResult, DetectionModeConfig
├── api/                # FastAPI REST API
│   ├── app.py          # FastAPI application factory
│   ├── deps.py         # Dependency injection (get_db)
│   ├── errors.py       # Exception handlers
│   ├── middleware.py    # Auth/rate-limit middleware stubs
│   ├── schemas.py      # API request/response schemas
│   └── routers/        # Route handlers (8 routers)
│       ├── analysis.py, days.py, devices.py, events.py
│       ├── rx.py, sessions.py, stats.py, waveforms.py
├── bootstrap/          # Installation and updates
│   ├── installer.py    # Global uv tool installation
│   ├── updater.py      # Version upgrade logic
│   └── version.py      # Version management
├── database/           # SQLAlchemy ORM layer
│   ├── models.py       # DB models (10 tables)
│   ├── session.py      # session_scope() context manager
│   ├── day_manager.py  # Day splitting and aggregation
│   ├── importers.py    # Session import pipeline
│   ├── types.py        # Database type extensions
│   └── migrations/     # Alembic migration infrastructure
│       └── env.py
├── parsers/            # Device parsers
│   ├── base.py         # DeviceParser abstract class
│   ├── registry.py     # parser_registry singleton
│   ├── register_all.py # register_all_parsers() — call at startup
│   ├── resmed_edf.py   # ResMed EDF+ parser
│   ├── unified.py      # UnifiedSession, WaveformData, RespiratoryEvent
│   ├── compression.py  # Data compression utilities
│   ├── discovery.py    # Data source discovery
│   ├── oscar_mappings.py # OSCAR channel mappings
│   ├── resmed_file_index.py # ResMed file indexing
│   ├── types.py        # Parser type definitions
│   └── formats/
│       ├── edf.py      # Generic EDF/EDF+ reader
│       └── types.py    # Format type definitions
├── services/           # Business logic layer (between CLI/API and database)
│   ├── schemas.py      # Service response schemas (Pydantic)
│   ├── analysis_facade.py   # Analysis orchestration
│   ├── backup_service.py    # Raw file backup
│   ├── database_service.py  # Database operations
│   ├── day_service.py       # Day aggregation
│   ├── device_service.py    # Device management
│   ├── event_service.py     # Event queries
│   ├── export_service.py    # Data export (CSV, JSON)
│   ├── lttb.py              # LTTB downsampling algorithm
│   ├── rx_service.py        # Prescription tracking
│   ├── session_service.py   # Session management
│   ├── stats_service.py     # Statistics calculations
│   └── waveform_service.py  # Waveform data access
├── utils/
│   └── display.py      # CLI display helpers
├── validation/         # Data validation
│   ├── batch.py        # BatchValidator
│   └── report.py       # ValidationReport
└── waveform/           # Waveform visualization
    ├── inspector.py    # Data loading and inspection
    └── renderer.py     # ASCII/plotext terminal rendering
ui/                     # Vue 3 + TypeScript + PrimeVue frontend
├── src/
│   ├── api/            # API client (8 modules matching routers)
│   ├── components/     # Reusable UI components (10+)
│   ├── composables/    # Vue composition functions
│   ├── views/          # Page views (7 views)
│   ├── router/         # Vue Router configuration
│   └── types/          # TypeScript type definitions
docs/
├── apnea_detection_reference.md  # Algorithm guide with Vancouver-style citations
├── manufacturers/resmed.md        # ResMed-specific documentation
├── roadmap.md                     # Project roadmap
└── references/                    # Research papers, clinical guidelines (9 PDFs)
    ├── README.md                  # Index with full citations
    └── PMC*.pdf                   # Open-access research papers
tests/
├── conftest.py         # Main fixtures
├── fixtures/           # Test data (recorded sessions, device data)
├── helpers/            # synthetic_data.py, validation_helpers.py
├── unit/               # Unit tests (auto-marked)
└── integration/        # Integration tests (auto-marked)
    └── test_api/       # API integration tests
```

## OSCAR Relationship

**OSCAR** (Open Source CPAP Analysis Reporter) is the upstream OSS project (Qt/C++ desktop GUI, 18+ manufacturers).

**Import paths:**
- **Direct (ResMed):** SD Card → SNORE → Database
- **Via OSCAR (all manufacturers):** SD Card → OSCAR → OSCAR Profiles dir → SNORE → Database

**What SNORE borrows from OSCAR:**
- Channel ID system (0x1000=Pressure, 0x1200=Flow, etc.) in `constants.py`
- Binary format parsers for `.000/.001` session files

**OSCAR parsers:** `parsers/oscar_device.py` (main), `oscar_summary.py`, `oscar_events.py`, `qdatastream.py`

## Tech Stack

- Python 3.13+ with UV package manager
- SQLAlchemy 2.0 ORM with SQLite (~/.snore/snore.db)
- Click CLI framework
- Pydantic for validation
- FastAPI + uvicorn (REST API)
- Vue 3 + TypeScript + PrimeVue (frontend)
- Rich (terminal formatting)
- Plotext (terminal charts)
- scipy (signal processing)
- Alembic (database migrations)
- pytest with coverage

## Key Patterns

**Parser Plugin Architecture:**
```python
# Inherit DeviceParser, implement 4 methods, register with parser_registry
class MyParser(DeviceParser):
    def get_metadata(self) -> ParserMetadata: ...
    def detect(self, path: Path) -> ParserDetectionResult: ...
    def get_device_info(self, path: Path) -> DeviceInfo: ...
    def parse_sessions(self, path, ...) -> Iterator[UnifiedSession]: ...
parser_registry.register(MyParser)
```

**Database Context Manager:**
```python
with session_scope() as session:
    # Auto-commit on success, rollback on exception
```

**Service Layer Pattern:**
Services sit between CLI/API and database. Constructor injection with SQLAlchemy session, typed Pydantic returns:
```python
class StatsService:
    def __init__(self, db_session: Session): ...
    def get_summary(self, days: int | None) -> StatsSummary: ...
```

**API Router Pattern:**
FastAPI routers use dependency injection for database sessions:
```python
router = APIRouter(prefix="/sessions", tags=["sessions"])

@router.get("/")
def list_sessions(db: Session = Depends(get_db)) -> list[SessionResponse]: ...
```

**Analysis Architecture:** Direct orchestration in `service.py`:
- BreathSegmenter → feature extraction → FlowLimitationClassifier → ComplexPatternDetector → PulseChangeDetector → EventDetector
- Event detection via modes with `DetectionModeConfig`:
  - `aasm` - AASM Scoring Manual v2.6 compliant (default, time-based baseline 120s, AASM_3PCT hypopneas with 3% SpO2 drop, RERA enabled, flow-only fallback)
  - `aasm_relaxed` - AASM with relaxed thresholds for machine matching (breath-based baseline 30 breaths, 85% validation, AASM_3PCT hypopneas, RERA enabled)
  - `resmed` - ResMed device algorithm matching (time-based baseline 120s, gap + low-flow detection, 50% apnea threshold, FLOW_ONLY hypopneas at 20% threshold, RERA enabled)
- Detected events: Obstructive Apnea (OA), Central Apnea (CA), Mixed Apnea (MA), Hypopnea (mode-dependent), RERA (flow-based without EEG)
- All apneas include `classification_confidence` (OA/CA/MA from flow-only is approximation)
- RDI calculation includes RERAs (AHI + RERAs/hour)
- All types use Pydantic models (validation, serialization)

**Unified Data Model:** All device data converts to `UnifiedSession` → `WaveformData` → `RespiratoryEvent`

## Code Style

- Type hints: `str | None` (not Optional), `list[str]` (not List), avoid `Any` types
- Imports: stdlib, third-party, then `snore.` absolute imports
- Naming: snake_case functions, PascalCase classes, UPPER_SNAKE constants
- All data types use Pydantic models (no dataclasses)

## Testing

**Standard workflow:**
```bash
just test                # All tests (standard pytest invocation)
just check-all           # Quality checks + tests
```

**Advanced pytest options (when needed):**
```bash
# Specific test subsets
uv run pytest tests/unit/                    # Unit only
uv run pytest tests/integration/             # Integration only
uv run pytest tests/integration/test_api/    # API integration tests
uv run pytest -m recorded                    # Tests using real device data
uv run pytest tests/unit/test_file.py -v    # Single file verbose
uv run pytest tests/ --cov=snore             # With coverage
```

Markers: `unit`, `integration`, `parser`, `recorded`, `real_data`, `slow`

Key fixtures: `db_session`, `test_device`, `test_session_factory`, `recorded_session("YYYYMMDD")`

## Common Gotchas

1. **OSCAR day-splitting logic:** Sessions before noon belong to previous day (e.g., 01:50 AM Dec 8 = Dec 7's sleep). Use `Day.date` for display/queries, not `session.start_time.date()`. See `day_manager.py:34-36` and `cli.py:1740-1743` (analysis show command uses day_date with fallback)
2. **Refresh after relationship changes:** `db_session.refresh(session)` after adding statistics
3. **Integration test isolation:** Use `reset_database_state()` autouse fixture pattern
4. **WAL cleanup:** Temp databases need `-wal` and `-shm` file cleanup
5. **Profile management removed:** Profiles are now optional (device-centric model). Days link directly to devices via `device_id`, not `profile_id`
6. **Type safety:** Use proper types (`list[BreathMetrics]` not `list[Any]`) - mypy strict mode enabled
7. **Pydantic validation:** Use `model_construct()` to bypass validation when testing invalid data
8. **Local development vs installed tool** - **CRITICAL**: Always use `uv run snore` when developing locally:
   - **Local dev (from repo)**: `uv run snore <command>` → runs YOUR local code changes directly
   - **Installed tool (any directory)**: `snore <command>` → runs installed version from `~/.local/share/uv/tools/`
   - Running `snore` without `uv run` will NOT reflect your local changes
   - **NEVER use editable install** (`uv pip install -e .`) - risks conflicts with installed version, unnecessary complexity
9. **RERA confidence capped at 0.7:** Flow-based RERA detection without EEG cannot exceed 0.7 confidence (true RERAs require EEG arousal). Detection uses ≥2 flow-limited breaths + recovery breath ≥50% amplitude increase.
10. **Apnea classification confidence:** OA vs CA vs MA classification from flow-only data is approximation (true classification needs thoracic/abdominal effort bands). All apneas include `classification_confidence` field (0-1) based on effort score distinctiveness.
11. **SpO2/Flow timestamp alignment:** `_detect_hypopneas()` validates SpO2 and flow signal lengths match before indexing. Mismatch logs warning and skips desaturation check to prevent IndexError with external oximeters at different sample rates.
12. **Documentation citations:** Use Vancouver-style numbered citations [1], [2] in `docs/apnea_detection_reference.md`. Add PDF to `docs/references/` and update both inline citation and References section. See existing format: author list, journal, year, volume, pages, DOI, PMCID, local path, URL.

## Key Files by Task

| Task | Files |
|------|-------|
| Add CLI command | `src/snore/cli.py` |
| Add device parser | `src/snore/parsers/base.py`, `src/snore/parsers/register_all.py`, create new parser file |
| Add analysis algorithm | `src/snore/analysis/shared/` (breath/feature algorithms) or `modes/` (event detection) |
| Add detection mode | `src/snore/analysis/modes/config.py` (add `DetectionModeConfig`), update `detector.py` |
| Modify event detection | `src/snore/analysis/modes/detector.py` (apnea/hypopnea/RERA detection logic) |
| Add event type | `src/snore/analysis/shared/types.py` (event models), update `detector.py`, add to `EventTimeline` |
| Tune detection thresholds | `src/snore/analysis/modes/config.py` (DetectionModeConfig fields), validate with `validate_against_machine_events()` |
| Modify data models | `src/snore/parsers/unified.py` (data), `database/models.py` (ORM), use Pydantic |
| Add waveform visualization | `src/snore/waveform/renderer.py` (ASCII/plotext), `inspector.py` (data loading) |
| Add API endpoint | `src/snore/api/routers/`, `api/schemas.py`, `api/deps.py` |
| Add/modify service | `src/snore/services/`, `services/schemas.py` |
| Frontend development | `ui/src/views/`, `ui/src/components/`, `ui/src/api/` |
| Export functionality | `src/snore/services/export_service.py`, `services/backup_service.py` |
| Data validation | `src/snore/validation/batch.py`, `validation/report.py` |
| Add test fixture | `tests/conftest.py`, `tests/helpers/` |
| Modify channel IDs | `src/snore/constants.py` (must align with OSCAR's schema.h) |
| Update algorithm documentation | `docs/apnea_detection_reference.md` (add inline citations [#]) |
| Add research reference | `docs/references/` (add PDF + update README.md index) |
