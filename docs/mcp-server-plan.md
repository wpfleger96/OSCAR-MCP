# SNORE MCP Server Plan — v2

MCP as a third thin presentation layer over the existing service layer, peer of the
CLI and FastAPI backend. Purpose-built for LLM-assisted PAP settings tuning.

**Sequenced behind the async migration.** MCP tools will be native-async from day one
(no retrofit). Dispatch begins after PR-2 merges.

---

## Context

~60% of the analysis machinery already exists: breath segmenter, 7-class flow-shape
classifier, RERA-proxy detector, `RxTracker` epochs, LTTB waveform downsampling. The
work is MCP wiring + per-breath persistence + three new query tools.

---

## Locked decisions

1. **Transport: stdio now, HTTP-ready by construction.** FastMCP serves both stdio and
   streamable-HTTP from the same tool definitions. We ship `snore mcp` (stdio); the
   future hosted mode is `snore mcp --http` plus auth — zero tool rewrites required.

2. **No backfill, no lazy path.** Breath features are computed in the import pipeline
   only. Drop-DB-and-reimport is the sole population mechanism and the recovery path
   if the algorithm version changes. One code path; import gets slightly slower when
   Phase 2 lands (timing reported at that PR).

3. **Journal/subjective: dropped.** `log_subjective`/`get_subjective` are out. The
   roadmap's Phase 3 journal item is a separate, unrelated thing.

---

## Phases

### Phase 1 — MCP skeleton + free tools

**Depends on:** async migration complete (PR-2 merged)

- `src/snore/mcp/` package, FastMCP, `snore mcp` CLI entry point
  (stdio; transport param plumbed for HTTP later)
- **`get_settings_timeline`** — adapter over `RxTracker`
- **`get_nightly_summary`** — adapter over `StatsService`/`DayService`, paginated
  (~30 nights/call); returns AHI split + RERA + leak/pressure percentiles + MV/RR/TV
- **`get_events`** — `EventService` + inline context per event: pressure/leak at
  event time, MV prior 120 s, minutes since session start
- Clinical-context instructions resource (reframed for UARS/RDI; AHI de-emphasised)
- Every field carries units; timestamps ISO 8601 with UTC offset

### Phase 2 — Breath-feature persistence

**Depends on:** async migration complete (can run parallel with Phase 1)

- New `breaths` table persisting per-breath metrics at import time:
  - `BreathMetrics` fields + `ShapeFeatures`
  - Flow class + confidence (7-class classifier)
  - Mid-inspiratory flattening index (alongside existing `flatness_index`; both
    versioned)
  - Recovery-breath flag
  - Inferred trigger/cycle type (flagged `experimental`, with confidence)
  - Per-breath quality flags: `leak_valid`, `ramp_active`, `mask_off`
  - Algorithm version stamp per row batch
- Alembic migration; populated only by fresh imports (reimport populates, no backfill)

### Phase 3 — Core tuning tools

**Depends on:** Phases 1 + 2

- **`get_breath_table`** — windowed (~15 min cap), binned aggregates beyond the cap
- **`find_windows`** — criteria queries over the `breaths` table:
  - Worst-N windows by flattening with leak-valid filter
  - Windows centred on central apnoeas
  - FL-run-ending-in-recovery-breath
- **`compare_epochs`** — `RxTracker` epochs × breath-feature distributions, leak-valid
  time only; median/IQR/95th + nights-per-epoch

### Phase 4 — Vision + TECA

**Depends on:** Phase 3

- **`render_window`** — server-rendered PNG (matplotlib) for short windows; enables
  visual review without the client downloading raw arrays
- **`get_waveform`** — raw downsampled arrays, tier-3 escape hatch (≤ 2 min,
  ≤ 1000 pts/channel)
- **`get_ca_analysis`** — per-CA preceding MV slope + PS delivered + stability;
  night-level periodic-breathing % and MV rolling variance (extends existing pattern
  detector); the ASV case-file tool

---

## Success criteria

Claude Desktop/Code connects over stdio and can, in one conversation:

1. Identify a night's worst flow-limitation windows with leak-valid filtering
2. Pull the breath table for one window
3. View the server-rendered PNG
4. Compare two settings epochs with real distribution stats
