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
- [x] OSCAR binary parser (partial - summary and events files)
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

## In Progress 🚧

### Statistics Enhancement
- [ ] **Multi-period summaries** - Week, month, 6-month, year reports
- [ ] **Trend analysis** - AHI trends, usage trends, effectiveness over time

### Data Discoverability
- [ ] **Waveform type selection** - Add `--type` option to `waveform show` (flow, leak, pressure, spo2, pulse, mv, rr, tv)
- [ ] **Multi-waveform view** - Support `--type flow,leak,pressure` to view multiple waveforms simultaneously
- [ ] **Waveform auto-discovery** - Show what waveform types are available for a session
- [ ] **Enhanced session display** - Add full statistics to `session show` (pressure, leak, SpO2/pulse, RR/TV/MV, REI)
- [ ] **Settings display** - Add `session show --settings` to view therapy settings (mode, pressure, EPR, ramp, humidity)
- [ ] **Stats detail enhancement** - Add SpO2 time below 90%, respiratory metrics summary to `stats` command

---

## Planned Features 📋

### Phase 1: Analysis Parity

**Goal:** Match OSCAR's core analysis capabilities

- [ ] **Records tracking** - Track best/worst metrics (AHI, leak, pressure, SpO2)
- [ ] **Session enable/disable** - Exclude sessions from statistics (bad data, testing)
- [ ] **Pulse change detection** - Detect pulse rate changes ≥ threshold for ≥ duration
- [ ] **Enhanced pattern detection** - Improve CSR/periodic breathing algorithms
- [ ] **RX change tracking** - Detect when therapy settings change between sessions
- [ ] **RX-to-event correlation** - Analyze how settings changes affect AHI/events

---

### Phase 2: Reporting & Data Management

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

### Phase 3: Web UI

**Goal:** Browser-based visualization and interaction

**Backend:**
- [ ] **FastAPI backend** - REST API exposing analysis endpoints
- [ ] **WebSocket support** - Real-time updates for long operations
- [ ] **Authentication** - Optional user authentication

**Frontend:**
- [ ] **Daily view** - Zoomable waveforms with event overlays
- [ ] **Multi-waveform visualization** - Display leak, pressure, SpO2, pulse alongside flow
- [ ] **Overview dashboard** - Trend charts, summary cards, calendar view
- [ ] **Statistics page** - Multi-period comparisons with configurable date ranges
- [ ] **Full statistics display** - Show all imported metrics (not just AHI)
- [ ] **Therapy settings panel** - Display device configuration and settings history
- [ ] **Event explorer** - Filter, search, and analyze events
- [ ] **Analysis comparison tool** - Compare machine vs programmatic detection
- [ ] **Session management** - Enable/disable sessions, add notes
- [ ] **Interactive graph customization** - Choose channels, colors, scales
- [ ] **Time range bookmarking** - Mark interesting sections for review
- [ ] **Minutes at pressure visualization** - Interactive histogram

**Technology stack:**
- Backend: FastAPI + SQLAlchemy
- Frontend: React/Vue + Plotly.js/Chart.js
- Deployment: Docker container

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

**Rationale:** Users can import non-ResMed devices via OSCAR, then export to OSCAR format for SNORE import (partial OSCAR parser already implemented).

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

