# SNORE Roadmap

Complete project overview showing implemented features and future development plans.

---

## Project Vision

**SNORE** (Sleep eNvironment Observation & Respiratory Evaluation) is a CLI-first sleep analysis tool with programmatic event detection and extensible analysis modes.

**Key principles:**
- **CLI-first:** Scriptable, automatable, fast iteration on algorithms
- **ResMed-focused:** Direct import from ResMed devices (other manufacturers via OSCAR export)
- **Programmatic analysis:** Configurable detection modes (AASM, AASM Relaxed, ResMed)
- **Future web UI:** Visualization layer consuming CLI/API

**Language:** Python 3.13+
**Database:** SQLite with Alembic migrations

---

## Completed Features ✅

### Core Infrastructure
- [x] SQLite database with Alembic migrations
- [x] Pydantic unified data model (UnifiedSession, DeviceInfo, WaveformData, RespiratoryEvent)
- [x] Parser registry with auto-detection and confidence scoring
- [x] Click-based CLI framework with rich output
- [x] Comprehensive logging system with rotation

### Data Import
- [x] **ResMed EDF+ direct import** (AirSense 10/11, AirCurve 10/11, S9)
  - STR.edf (settings), BRP.edf (flow), PLD.edf (pressure/leak), SA2.edf (oximetry), EVE.edf (events)
- [x] Noon-to-noon session grouping (matches ResMed/OSCAR behavior)
- [x] Parallel parsing with ThreadPoolExecutor
- [x] Session segment merging (handles mask removal/replacement)
- [x] Discontinuous EDF (EDF+D) support
- [x] OSCAR binary parser (full support - all OSCAR-cached devices)
- [x] Date range filtering (--from/--to)
- [x] Import sort modes (date-asc, date-desc, filesystem)

### Analysis Pipeline
- [x] **Breath segmentation** via zero-crossing detection with hysteresis
- [x] **Per-breath metrics:**
  - Tidal volume (mL) with 5-point smoothing
  - Peak inspiratory/expiratory flow (L/min)
  - Inspiration/expiration time (Ti/Te)
  - I:E ratio
  - Respiratory rate (per-breath and 60s rolling)
  - Minute ventilation (L/min)
- [x] **3 detection modes:**
  - AASM (AASM Scoring Manual v2.6 compliant)
  - AASM Relaxed (30-breath baseline, relaxed thresholds)
  - ResMed (approximates ResMed machine logic)
- [x] **Hypopnea detection modes:**
  - AASM_3PCT (30% flow + 3% SpO2)
  - AASM_4PCT (30% flow + 4% SpO2, CMS/Medicare)
  - FLOW_ONLY (40% flow reduction only)
- [x] **Flow limitation** 7-class severity classification
- [x] **CSR/periodic breathing** programmatic detection
- [x] **SpO2 drop detection** (3%/4% modes)
- [x] Feature extraction (shape, spectral, flatness, plateau)

### CLI Commands
- [x] `snore import PATH` - Import CPAP data with parallel processing
- [x] `snore session list/show/delete` - Session management
- [x] `snore analysis run/list/show/delete` - Programmatic analysis
- [x] `snore waveform show/compare` - Waveform visualization
- [x] `snore event export` - Export events to CSV
- [x] `snore db init/stats/vacuum/drop` - Database management
- [x] `snore stats` - Therapy statistics summary (single period)
- [x] `snore validate` - Batch validation across sessions
- [x] `snore profile create/list/show/delete/set-default` - Profile management (optional)
- [x] `snore config show` - Show configuration
- [x] `snore logs show/clear/path` - Log management
- [x] `snore completions install/uninstall/bash/zsh` - Shell completion
- [x] `snore setup` - Global installation via uv tool
- [x] `snore upgrade` - Version upgrade
- [x] `session show --settings` - View therapy settings (mode, pressure, EPR, ramp, humidity)

### Visualization
- [x] **ASCII waveform renderer** - Pure terminal rendering, any terminal size
- [x] **Plotext terminal charts** - Higher-resolution plots
- [x] **Interactive mode** - Vim-style navigation (h/j/k/l for pan/zoom)
- [x] **Event overlay annotations** - Visual markers on waveforms
- [x] **CSV export from visualization** - Data export from rendered views

### Database Schema
- [x] Tables: profiles, devices, days, sessions, waveforms, events, statistics, settings
- [x] Day-level aggregation with pre-calculated statistics
- [x] Analysis results storage (programmatic_result_json)
- [x] Settings tracking per session

---

## Recently Completed ✅

### Phase 2.1: FastAPI Backend (2026-02-17)
- [x] **8 routers, 24 REST endpoints** - Full service layer exposed via HTTP API
- [x] **`snore serve` CLI command** - Starts uvicorn with host/port/reload/db options
- [x] **Two new services** - `RxService`, `DayService` bridge dataclasses/ORM to Pydantic
- [x] **Caller-controlled transactions** - `get_db()` FastAPI dependency; commits removed from services
- [x] **`NotFoundError` domain exception** - Replaces `ValueError` catch-all; genuine `ValueError` → 500
- [x] **76 new integration tests** - Total test count: 573 (up from 497)
- [x] **OpenAPI docs** - Auto-generated at `/docs`; 204 responses documented via `responses=` param
- [x] **LTTB downsampling** - 720k-point waveforms served in <100ms via `max_points` query param

### Phase 2.0b: Service Layer Extraction (2026-02-14)
- [x] **8 services extracted from CLI** - DatabaseService, SessionService, StatsService, WaveformService, EventService, AnalysisFacade, DeviceService, LTTB downsampling (1,741 lines moved)
- [x] **CLI reduced by 7.2%** - From 3,797 to 3,524 lines
- [x] **93 new unit tests** - Total test count: 497 (up from 404)
- [x] **API-ready services** - Constructor injection, typed Pydantic returns, transaction boundary docs
- [x] **Comprehensive smoke test** - 57 CLI commands validated across all services

### Phase 2.0a: Dead Code Cleanup (2026-02-12)
- [x] **Deleted 7 dead modules** - ~1000+ lines of unused code removed
- [x] **Model layer consolidated** - "Types live with the code that owns them" principle
- [x] **Eliminated models/ package** - Pydantic types organized by ownership (parsers/unified.py, analysis/**/types.py, services/schemas.py)

### Statistics Enhancement (2026-02-08)
- [x] **Multi-period summaries** - Week, month, 6-month, year reports (`stats --period`)
- [x] **Trend analysis** - AHI trends over time with plotext charts (`stats --trend`)

### Data Discoverability (2026-02-08)
- [x] **Waveform type selection** - Add `--type` option to `waveform show` (flow, leak, pressure, therapy_pressure, epap, spo2, pulse, mv, rr, tv)
- [x] **Multi-waveform view** - Support `--type flow,leak,pressure` to view multiple waveforms simultaneously with stacked subplots
- [x] **Waveform auto-discovery** - `waveform list` command shows available waveform types for a session
- [x] **Enhanced session display** - Full statistics in `session show` (pressure, EPAP, leak, SpO2/pulse, RR/TV/MV, REI)
- [x] **Stats detail enhancement** - SpO2 time below 90%, respiratory metrics (RR/TV/MV), pulse, REI in `stats` command

### Pressure Data Model (2026-02-08)
- [x] **Therapy pressure vs mask pressure separation** - THERAPY_PRESSURE, MASK_PRESSURE, and EPAP as distinct waveform types
- [x] **ResMed PLD parser fix** - Correctly extracts all 3 pressure signals (Press.2s, MaskPress.2s, EPRPress.2s)
- [x] **STR.edf daily summaries** - Import machine-computed percentiles (MaskPress, TgtEPAP, Leak, RR, TV, MV)
- [x] **OSCAR pressure channels** - Import therapy pressure and EPAP from OSCAR binary database
- [x] **OSCAR device settings** - Import EPR level, therapy mode, pressure min/max from OSCAR summary

### Phase 1: Analysis Parity (2026-02-08)
- [x] **Session enable/disable** - Exclude sessions from statistics (bad data, testing)
  - Add enabled flag to Session model (defaults to true)
  - Filter disabled sessions from day aggregation and statistics
  - CLI: `snore session enable/disable <id>` with automatic day recalculation
  - CLI: `snore session list --all` to show disabled sessions
- [x] **Records tracking** - Track best/worst metrics (AHI, leak, pressure, SpO2)
  - Top 5 best/worst days for AHI (lowest/highest), leak (lowest), therapy hours (longest/shortest), SpO2 min (highest/lowest)
  - CLI: `snore stats --records`
- [x] **Pulse change detection** - Detect pulse rate changes ≥ threshold for ≥ duration
  - OSCAR-compatible algorithm: ≥5 BPM changes within 8-second sliding windows
  - Integrated into analysis pipeline with pulse_change_count and pulse_change_index
- [x] **Enhanced pattern detection** - Improve CSR/periodic breathing algorithms
  - Windowed episode detection (10-min windows, 2-min steps)
  - Time-localized episodes instead of whole-session summaries
  - Improved waxing-waning scoring with envelope amplitude ratio check
- [x] **RX change tracking** - Detect when therapy settings change between sessions
  - Groups consecutive days by therapy settings (mode, pressure, EPR)
  - CLI: `snore rx history` shows all RX periods with settings and avg AHI
  - CLI: `snore rx current` shows current settings period
- [x] **RX-to-event correlation** - Analyze how settings changes affect AHI/events
  - Compute AHI, leak, and usage statistics per RX period
  - Identify best/worst settings periods (min 7 days filter)
  - CLI: `snore rx compare` shows comparison table with best/worst markers

## In Progress 🚧

### Phase 2: Web UI

**Sub-phase 2.0a: Dead Code Cleanup & Model Consolidation** ✅ COMPLETE (2026-02-12)
- [x] Deleted 7 dead modules (~1000+ lines of unused code)
- [x] Model layer consolidated following "types live with the code that owns them" principle
- [x] Eliminated `models/` package entirely
- [x] Pydantic type ownership model: parsers/unified.py (parser types), analysis/**/types.py (domain types), services/schemas.py (service responses)

**Sub-phase 2.0b: Service Layer Extraction** ✅ COMPLETE (2026-02-14)
- [x] Extracted 8 services from CLI monolith (1,741 lines of business logic)
  - DatabaseService, SessionService, StatsService, WaveformService, EventService, AnalysisFacade, DeviceService, LTTB downsampling
- [x] CLI reduced from 3,797 to 3,524 lines (-7.2%)
- [x] 93 new unit tests added (497 total tests, up from 404)
- [x] SQL parameterization, Pydantic V2 migration, transaction boundary documentation
- [x] Comprehensive CLI smoke test passed (57/57 commands, zero errors)
- [x] Services are API-ready with typed Pydantic returns and constructor injection

**Sub-phase 2.1: FastAPI Backend** ✅ COMPLETE (2026-02-17)
- [x] REST API with 8 routers and 24 endpoints (`snore serve`)
  - Sessions (list, detail, enable/disable, delete, delete-preview)
  - Waveforms (list types, get data with LTTB downsampling)
  - Events (list, match machine vs programmatic)
  - Analysis (list status, get result, run, delete, delete-preview)
  - Stats (summary, periods, trends, records)
  - Devices (list)
  - Days (list, detail)
  - RX (history, current, compare)
- [x] New services: `RxService`, `DayService` (thin Pydantic bridges over `RxTracker` and Day ORM)
- [x] Caller-controlled transactions (`get_db()` dependency handles commit/rollback)
- [x] `NotFoundError` domain exception in `snore.exceptions` (replaces `ValueError` catch-all)
- [x] Auth/rate-limit middleware stubs designed for production swap-in
- [x] CORS configured for Vue dev server (`localhost:5173`)
- [x] OpenAPI auto-generated docs at `/docs`
- [x] 573 tests total (76 new API integration tests)

**Sub-phase 2.2-2.4: Vue Frontend** 🚧 NEXT
- [ ] Interactive waveform viewer with zoom/pan
- [ ] Dashboard with summary cards and trend charts
- [ ] Event explorer and analysis comparison tools

---

## Planned Features 📋

_(Phase 2: Web UI moved to "In Progress" section above)_

---

### Phase 3: Reporting & Data Management

**Goal:** Generate reports and manage data lifecycle

- [ ] **HTML statistics report** - Multi-period reports with charts and tables
- [ ] **CSV export enhancements:**
  - Summary mode: Per-day aggregated statistics
  - Sessions mode: Per-session data
  - Details mode: Events with full session context
  - Export all waveform types to CSV (not just flow)
  - Include therapy settings in session exports
  - Include all statistics fields in summary exports
- [ ] **PDF report generation** - Printable reports with graphs
- [ ] **Journal/notes per day** - User annotations and observations
- [ ] **Database backup/restore** - Export/import database with integrity checks
- [ ] **Minutes at pressure distribution** - Histogram of time spent at each pressure level

---

### Phase 4: Enhanced Analysis

**Goal:** Extend analysis algorithms beyond current capabilities

- [ ] **Additional hypopnea modes** - User-configurable thresholds and criteria
- [ ] **Sleep efficiency estimates** - Calculate efficiency from session times and gaps
- [ ] **Advanced flow analysis** - Flow shape clustering, inspiratory/expiratory pattern analysis
- [ ] **Predictive modeling** - AHI prediction based on settings changes

---

### Phase 4.5: Performance Optimization

**Goal:** Further optimize import pipeline for faster session parsing

- [ ] **Parallel file parsing within sessions** - Parse SA2/BRP/PLD files concurrently using ThreadPoolExecutor
  - Refactor `_parse_statistics()` to return data instead of mutating session object
  - Refactor `_parse_breathing_waveforms()` to return data
  - Refactor `_parse_pressure_leak()` to return data
  - Merge results from parallel parsing into session object

**Note:** Phases 1-4 of import optimization already complete (3-5x speedup achieved). This is the remaining work.

---

### Phase 5: Oximetry

**Goal:** Standalone oximeter support and enhanced SpO2 analysis

- [ ] **Standalone oximeter file import:**
  - Contec CMS50D+ (SpOR files)
  - Viatom/Wellue (native format)
  - ChoiceMMed MD300W1 (DAT files)
- [ ] **Live oximeter streaming** - Real-time SpO2/pulse via serial port (pyserial)
- [ ] **SpO2/pulse correlation** - Correlate desaturations with respiratory events
- [ ] **Oximetry-specific statistics:**
  - ODI (Oxygen Desaturation Index)
  - Average SpO2, time below thresholds
  - Pulse variability metrics

---

### Phase 6: Sleep Staging

**Goal:** Integrate sleep stage data from EEG devices

- [ ] **Zeo CSV import** - Sleep stages (Wake, REM, Light, Deep), ZQ score, awakenings
- [ ] **Dreem CSV import** - Sleep stages, sleep metrics, headband data
- [ ] **Sleep stage overlay** - Display stages on waveforms
- [ ] **Per-stage event analysis:**
  - AHI during REM vs NREM
  - Flow limitation by sleep stage
  - Positional effects by stage
- [ ] **Sleep efficiency calculations** - SE, total sleep time, WASO
- [ ] **Arousal data** - Import EEG-based arousals from Dreem/Zeo (not ResMed RERA)

**Note:** ResMed CPAP devices do NOT have EEG sensors. "Arousal" events in ResMed data are RERA (flow-based), already imported.

---

### Phase 7: Position Tracking (via Somnopose)

**Goal:** Analyze positional effects on sleep apnea

- [ ] **Somnopose CSV import** - Orientation, inclination, movement
- [ ] **Position overlay** - Display position data on waveforms
- [ ] **Positional AHI analysis:**
  - AHI supine vs lateral vs prone
  - Event type breakdown by position
  - Time in each position
- [ ] **Position-correlated statistics** - Pressure, leak, SpO2 by position

**Note:** ResMed CPAP devices do NOT have position sensors. Requires external device (Somnopose or phone app).


---

## Out of Scope (Use OSCAR)

**Not planned for SNORE:**
- Direct import for non-ResMed CPAP devices (Philips, Fisher & Paykel, DeVilbiss, etc.)
- Live device communication (SNORE imports from SD card only)
- Proprietary binary format reverse-engineering (DreamStation 2 encrypted files, etc.)

**Rationale:** Users can import non-ResMed devices via OSCAR, then export to OSCAR format for SNORE import (full OSCAR parser implemented).

---

## Architecture Decisions

### Why ResMed-only direct import?

**Pros:**
- ResMed uses standard **EDF+ format** (well-documented, pyedflib support)
- No reverse-engineering required
- Clean separation: parser reads standard format → unified model

**Cons of supporting other manufacturers:**
- Philips uses **proprietary encrypted formats** (AES-GCM, reverse-engineered)
- Fisher & Paykel, DeVilbiss, Lowenstein all have custom binary formats
- Maintenance burden of 10+ device parsers

**Solution:** Users import other devices via OSCAR desktop app, then use SNORE's OSCAR parser.

---

### Why CLI-first?

**Benefits:**
- **Scriptable** - Automate analysis workflows, batch processing
- **Fast iteration** - No UI dependencies for algorithm development
- **Testing** - Easy to test CLI commands vs GUI interactions
- **Power users** - Direct access to all functionality
- **Future web UI** - Can consume same analysis engine

**When web UI arrives:** CLI remains as automation/scripting interface.

---

### Why SQLite?

**Benefits:**
- **Zero configuration** - No database server setup
- **Portable** - Single file, easy backup
- **Sufficient performance** - Single-user workloads, millions of rows
- **Easy migration** - Alembic schema evolution

**Limitations accepted:**
- No concurrent writes (single user = not a problem)
- No built-in replication (backup via file copy)

---

## Data Availability Notes

**Verified via OSCAR loader analysis:**

| Feature | ResMed Data Available? | Implementation Plan |
|---------|----------------------|---------------------|
| RERA events | ✅ Yes (as "Arousal" annotation) | Already imported as RERA |
| EEG-based arousals | ❌ No (requires external EEG device) | Phase 6: Import from Dreem/Zeo |
| Body position | ❌ No (ResMed has no accelerometer) | Phase 7: Import from Somnopose |
| Flow waveform | ✅ Yes (BRP.edf, 25 Hz) | Already imported |
| Pressure waveform | ✅ Yes (PLD.edf, 0.5 Hz) | Already imported |
| SpO2/Pulse | ✅ Yes (SA2.edf if oximeter attached) | Already imported |
| Respiratory rate | ✅ Yes (PLD.edf, calculated by machine) | Already imported |
| Minute ventilation | ✅ Yes (PLD.edf, calculated by machine) | Already imported |
| Tidal volume | ✅ Yes (PLD.edf, calculated by machine) | Already imported |

