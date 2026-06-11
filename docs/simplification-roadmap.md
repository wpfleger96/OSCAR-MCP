# Simplification Roadmap

This document records the codebase-wide simplification effort: why it was done, what
changed, the design decisions behind it, and the items that were deliberately left
alone. It is the companion to two stacked PRs (backend simplification, then UI +
docs).

## Why

SNORE's essence is a **pipeline**: device files → unified model → SQLite → analysis →
presentation. Its soul is the analysis core — breath segmentation, AASM-compliant
event detection, validation against machine scoring. Everything else is delivery
plumbing. Three principles drove every change:

1. **Architecture mirrors the pipeline; the algorithmic core stays pure.**
   *Functional core, imperative shell.* Detection logic should be importable,
   testable, and comparable without a database or CLI attached — that is what makes
   `validate` / `waveform compare` results trustworthy.
2. **Duplication has a cause; fix the cause.** This repository is developed heavily
   with coding agents, which replicate existing patterns rather than abstract them.
   A one-time cleanup re-accretes unless the consolidated idioms become the
   documented convention (see the AGENTS.md updates) that future sessions are
   steered toward.
3. **The schema layers are one model in three costumes.** The unified parser
   statistics model, the services response model, and the ORM table align
   field-for-field by name. The hand-written conversion code between them was
   accidental complexity. The fix makes the isomorphism explicit
   (`model_dump()` / `model_validate(from_attributes=True)`) and machine-enforced
   (a drift-guard test fails if the models ever diverge).

## Headline numbers

- Backend: 72 files changed, **2,324 insertions / 2,593 deletions** (excluding lockfile)
- UI: 27 files changed, **483 insertions / 606 deletions** (excluding lockfile/generated types)
- `analysis/modes/detector.py`: **1,491 → 939 lines**, with cohesive extractions into
  `baseline.py`, `postprocess.py`, `classification.py`
- `parsers/resmed_edf.py`: **2,016 → 1,947 lines** with 7 duplicated waveform blocks
  collapsed into one `_read_waveform` helper
- `parsers/registry.py`: 234 → 199 lines; `parsers/qdatastream.py`: 423 → 352 lines
  (grep-verified dead code)
- Full suite green throughout: 683 baseline → **685 tests** (two drift-guard tests added),
  mypy strict, ruff lint + format, UI type-check/lint/build

## Bugs found along the way (not simplification, but load-bearing)

1. **`parsers/discovery.py:64` was a SyntaxError on the default branch** —
   `except PermissionError, OSError:` (Python 2 syntax) shipped in a "ruff fixes"
   style commit. The entire parsers package failed to import. Nothing in CI caught
   it because no test imports `discovery` directly. *Recommendation: add an
   import-smoke step (e.g. `python -c "import snore.cli"`) or rely on the now-fixed
   UI/backend CI split plus coverage of `snore import`.*
2. **UI type-checking was a silent no-op.** `vue-tsc --noEmit` against the
   solution-style root `tsconfig.json` (`"files": []`) checked nothing, in CI and in
   the build. Fixed to `vue-tsc --build`; this immediately surfaced the optionality
   drift fixed in the type-codegen work.
3. **Raw-SQL date boundaries were subtly wrong.** `list_sessions` compared
   `from_date.isoformat()` strings (`...T12:00:00`) against SQLite-stored datetimes
   (`... 12:00:00.000000`, space separator); since `" " < "T"`, boundary comparisons
   misbehaved at exact cutoffs. The ORM rewrite binds real datetimes and is correct.

## What changed (by workstream / commit group)

### 1. Schema isomorphism (`refactor(importers)`, `refactor(services)`, `test:` drift guard)
- `_import_statistics` is now a one-line `model_dump()` splat (45 hand-written field
  assignments deleted); `_import_settings` uses
  `model_dump(mode="json", exclude={"ps", "other_settings"}, exclude_none=True)`.
- Services `SessionStatistics` hydrates via `model_validate(stats_record)`
  (`from_attributes=True`); 34-line mapping block deleted.
- `tests/unit/test_schema_alignment.py` asserts the Pydantic field sets are subsets
  of the ORM columns — the executable invariant that keeps the splat safe.

### 2. Parser layer (`refactor(parsers)`, `build(deps)`)
- `_read_waveform` replaces 7 near-identical EDF signal-reading blocks
  (valid-range masking for SpO2/Pulse, L/s→L/min conversion, stats-over-valid-subset
  semantics all preserved exactly).
- Shared `extract_basic_stats` helper; unit-string constants in `constants.py`
  (both `cmH2O` and OSCAR's `cmH₂O` kept — normalizing stored DB data would require
  a migration, so the two-constant design is a decision, not an oversight).
- ResMed event annotation labels moved to `parsers/event_labels.py` (module-level,
  shareable). OSCAR's integer-channel map deliberately NOT merged — different key
  domain.
- `parse_sessions` decomposed into `_discover_session_files` /
  `_parse_single_session_bundle` + orchestration.
- Registry: never-queried `_parsers_by_id` and the `_parsers_by_manufacturer`
  optimization (2 registered parsers) deleted, `manufacturer_hint` parameter removed.
- qdatastream/unified: grep-verified dead methods deleted
  (`read_uint16`, `read_qhash_uint32_qvariant`, `skip_qhash_uint32_qvariant`,
  `tell`, `seek`, `skip_bytes`, `get_waveform`, `has_waveform`).
- `mne` demoted to the `edf-discontinuous` optional extra (it was already
  lazily imported, used only for EDF+D discontinuous files, ~1-2% of sessions);
  kept in the dev group so tests can exercise the fallback. A clear install hint is
  raised if a discontinuous file is hit without it.

### 3. CLI + tooling (`refactor(cli)`, `test:`, `ci:`)
- `print_table` display helper replaced five hand-rolled f-string table sites
  (output byte-compared; one pre-existing 1-char row misalignment fixed).
- `db_session()` context manager replaced the `init_db` + `session_scope` preamble
  in 19 commands (2 kept direct calls because tests patch `init_db` as a module
  attribute there).
- Composite `device_option` / `session_id_date_options` decorators for verbatim
  3+-site repeats; completion-script printing deduplicated.
- conftest consolidation: marker hooks merged into the root conftest, four duplicate
  `reset_database_state` autouse fixtures merged into `tests/integration/conftest.py`,
  grep-verified dead fixtures/helpers deleted (`initialized_db`, `add_noise`,
  `add_artifacts`, `create_multi_segment_session`, `assert_no_data_corruption`,
  `compare_breaths`, `assert_smoothed_close_to_raw`, `assert_rolling_window_accurate`).
- CI split into parallel `backend` and `ui` jobs.

### 4. Detector decomposition (`refactor(analysis)`, `refactor(validation)`)
- `EventDetector` public API unchanged; cohesive groups extracted as module-level
  functions taking `DetectionModeConfig` explicitly:
  `modes/baseline.py` (baseline computation), `modes/postprocess.py`
  (overlap/validate/dedupe/merge + event matching), `modes/classification.py`
  (apnea-type classification, effort estimation, confidence, desaturation).
- Event-matching tolerance is single-sourced (`EVENT_MATCH_TOLERANCE_SECONDS = 5.0`
  in `postprocess.py`). The CLI display layer no longer re-implements
  false-negative/false-positive matching with its own hardcoded tolerance — it
  consumes the richer `validate_against_machine_events` return.
- `EventService`: `db_session` required; pure `match_events` / `classify_matches`
  are static; missing sessions raise `NotFoundError` instead of returning `None`.

### 5. ORM as the single query idiom (`refactor(sessions)`, `perf(analysis)`)
- `SessionService.list_sessions` rewritten from f-string SQL assembly to typed
  SQLAlchemy selects with a shared `_session_filters` builder.
- `AnalysisFacade` N+1 fixed (one `row_number() OVER (PARTITION BY session_id)`
  query instead of a per-session latest-analysis lookup); list/count share a
  `_status_query` builder; both `get_delete_preview`s converted to ORM.
- Remaining known raw SQL: `AnalysisFacade.delete_analysis` (parameterized `text()`,
  no string assembly) — acceptable; convert opportunistically.

### 6. Service consolidation (`refactor(services)`, `refactor(api)`, `refactor(waveform)`)
- `DeviceService` merged into `DatabaseService` (system/metadata service);
  `RxService` collapsed into `RxTracker` (returns Pydantic responses directly).
  Two service modules deleted; tests migrated 1:1.
- `DayService` hydrates via `from_attributes`; duplicate conversion removed.
- `api/deps.py`: `service_dep(cls)` dependency factory adopted across routers;
  `DateRangeParams.start_datetime/end_datetime` properties replace repeated
  `datetime.combine` boilerplate.
- Get-or-404 convention: services raise `NotFoundError` (mapped to 404 by the
  registered handler); redundant router-side `None` checks deleted.
  `AnalysisFacade.get_analysis_result` intentionally still returns `None` — five
  callers treat "not yet analyzed" as a normal state, not an error.
- `export_csv` / `export_json` share `_build_export_sessions()` (outputs verified
  byte-identical on a synthetic DB).
- `WaveformInspector` no longer constructs its own loader; it delegates windowing to
  `WaveformService` (single high-level access point; `WaveformLoader` remains the
  low-level deserialization layer, still used directly by `analysis/service.py`).

### 7. UI consolidation (`feat(ui)`, `refactor(ui)`)
- **OpenAPI type codegen**: `scripts/export_openapi.py` + `npm run generate:types`
  (`just ui-generate-types`) produce `ui/src/types/generated.ts`; 20 hand-written
  API types became re-exports of `components['schemas']`. Hand-written API types can
  no longer drift. Codegen immediately caught systemic optionality drift
  (backend-optional fields typed as required in TS).
- `createApiEndpoint` helper: all 8 API modules are one-expression endpoint
  definitions; exported names/signatures unchanged.
- `useApiLoad` composable adopted in the four structurally identical view loaders;
  three views with genuinely bespoke flows (paging commit, staged side effects,
  404-driven state machine) intentionally stay custom.
- `ui/src/utils/formatting.ts` consolidates 8 duplicate date/time formatters.
- Backend gap worth closing someday: `/stats/trends`, `/stats/records`, and
  `/sessions/{id}/analysis` return untyped payloads, so their TS types remain
  hand-written (`TrendData`, `RecordsData`, `AnalysisResult`).

## Design decisions

| Decision | Rationale |
|---|---|
| ORM is the single query idiom | Typed under mypy strict, consistent with the rest of the service layer, no f-string SQL assembly; these are simple 2–3 table joins on local SQLite. |
| Two pressure-unit constants (`cmH2O`, `cmH₂O`) | OSCAR-imported rows store the Unicode form in the DB; normalizing stored data needs a migration. Both spellings are now named constants instead of scattered literals. |
| `AnalysisFacade` stays separate from `SessionService` | It filters on `Day.date`, not `start_time` — shared *idiom*, not a forced shared function. |
| Event fields `peak_flow_limitation` / `spo2_drop` kept | Written but never read; they are persisted schema. Removing them is a DB migration decision, out of scope for behavior-preserving refactors. |
| OSCAR vs ResMed event maps not merged | Integer channel-ID keys vs device label strings — a forced common abstraction would be worse than the duplication. |
| Device-info extraction not unified | ResMed (JSON → EDF-header regex) and OSCAR (XML → directory name) share only "try structured, fall back" control flow; a helper would save <10 lines. |
| `completions.py` marker-based install kept as-is | Evaluated; works, tested, and a library dependency would not simplify it. |

## Verification approach

Every workstream gated on the full suite (`pytest`, 685 passed), mypy strict, ruff
lint + format, and where relevant: byte-compared CLI output, byte-identical export
files, fixture-equality on parser imports, bit-identical detection results, and
`--help` diffs. The UI gate is `vue-tsc --build`, eslint (0 warnings), prettier, and
a production build.

## Future opportunities (not done here)

- Type the three untyped API endpoints so the last hand-written TS API types can be
  generated.
- Convert `AnalysisFacade.delete_analysis` raw SQL to ORM when next touched.
- `DayManager.create_or_update_day` has no `src/` callers (tests only) — candidate
  for removal in a change that also owns those tests.
- An import-smoke CI step to catch syntax errors in rarely-imported modules.
- A pre-existing circular import (`snore.waveform` ↔ `cli.display`) is latent;
  untouched here, worth untangling if it ever bites.
