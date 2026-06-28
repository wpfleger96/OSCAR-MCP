# PLAN__UI_OVERHAUL

## Overview

Overhaul the SNORE web UI by replacing PrimeVue 4 (Aura theme) with shadcn-vue + Tailwind CSS v4 for a modern aesthetic, and building out full CLI parity by extracting shared backend services and adding new API endpoints + UI views. The current UI covers ~30% of the CLI's 30 commands (7 views, ~3,500 lines hand-written Vue 3 + TypeScript). The CLI's primary workflow (data import), all exports, batch analysis, validation, and database management are entirely absent from the web UI.

Key architectural decision: CLI commands that currently bypass the service layer (import, batch analysis, waveform compare, db vacuum) will be refactored to use the same shared services as the new API endpoints. Both CLI and API become thin wrappers over the service layer — same behavior, same code path. This was chosen over the alternative of having the CLI call the API's HTTP endpoints, which would add unnecessary network overhead for a local tool and create a circular dependency (the CLI starts the API server via `snore serve`).

Design system choice: shadcn-vue (v2.7.4, 60+ components, built on Reka UI headless primitives, TanStack Table for DataTable) was selected over PrimeVue Volt (lower effort but less polished aesthetic) and Nuxt UI v4 (feature-rich but less battle-tested as standalone Vue). The current PrimeVue footprint is only 11 component imports (`Button`, `Column`, `DataTable`, `DatePicker`, `Dialog`, `Panel`, `Select`, `SelectButton`, `Tag`, `ToggleButton`, config), making the swap manageable.

## Scope

**Features:**
- Design system swap: PrimeVue -> shadcn-vue + Tailwind CSS v4 with dark mode
- CLI parity: Import, Export (raw/csv/json), Batch Analysis, Validation, DB Management, Waveform Compare
- Enhanced existing views: Dashboard (full summary data), Session Detail (all 30+ stats fields), Session List (column sorting, bulk delete)
- New views: Import, Export, Analysis Management, Database Management, Validation, Day Detail

**Components:**

Backend services (new or extended):
- `src/snore/services/import_service.py` - New service extracting import pipeline from CLI; handles both filesystem and file-upload flows
- `src/snore/services/analysis_facade.py` - Extended with `run_batch_analysis` for parallel multi-session analysis
- `src/snore/services/waveform_service.py` - Extended with `compare_events` for machine vs programmatic event comparison
- `src/snore/services/database_service.py` - Extended with `vacuum` method
- `src/snore/services/schemas.py` - New Pydantic models for import, batch analysis, event comparison, validation

API routers (new or extended):
- `src/snore/api/routers/import_data.py` - New router for import detect + upload endpoints
- `src/snore/api/routers/export.py` - New router for CSV/JSON/raw export download endpoints
- `src/snore/api/routers/db.py` - New router for database stats + vacuum endpoints
- `src/snore/api/routers/validation.py` - New router for batch validation endpoint
- `src/snore/api/routers/analysis.py` - Extended with batch analysis endpoint
- `src/snore/api/routers/waveforms.py` - Extended with event comparison endpoint
- `src/snore/api/routers/sessions.py` - Extended with flexible delete-preview endpoint
- `src/snore/api/app.py` - Register new routers (follows existing pattern: `app.include_router()` with prefix + tags)

CLI refactoring:
- `src/snore/cli/commands/import_data.py` - Refactor to delegate to `ImportService`; keep prompting + Rich display
- `src/snore/cli/groups/analysis.py` - Refactor `_analyze_batch` to delegate to `AnalysisFacade.run_batch_analysis`
- `src/snore/cli/groups/waveform.py` - Refactor `compare_events` to delegate to `WaveformService.compare_events`
- `src/snore/cli/groups/db.py` - Refactor vacuum to delegate to `DatabaseService.vacuum`

Frontend (new or modified):
- `ui/src/views/ImportView.vue` - Multi-step import form with directory picker, options, progress, results
- `ui/src/views/ExportView.vue` - Export form with format/date/device selection and file download
- `ui/src/views/AnalysisManagementView.vue` - Top-level analysis list, batch run, delete management
- `ui/src/views/DatabaseView.vue` - DB stats display + vacuum action
- `ui/src/views/ValidationView.vue` - Batch validation form + results table
- `ui/src/views/DayDetailView.vue` - Day summary with session list and per-metric stats
- `ui/src/views/DashboardView.vue` - Enhanced with event breakdown, SpO2, pulse, respiratory metrics
- `ui/src/views/SessionDetailView.vue` - Enhanced with all 30+ statistics fields in categorized layout
- `ui/src/views/SessionListView.vue` - Column sorting via TanStack Table, multi-select bulk delete
- `ui/src/components/ui/` - shadcn-vue components (code-ownership model, copied into project)
- `ui/src/components/DataTable.vue` - Reusable TanStack Table wrapper supporting server-side pagination
- `ui/src/composables/useDarkMode.ts` - Dark mode toggle composable
- `ui/src/lib/utils.ts` - `cn()` helper (clsx + tailwind-merge)
- `ui/src/api/import.ts`, `export.ts`, `db.ts`, `validation.ts` - New API client modules

**CLI-only features (not planned for web UI):**
- `snore setup` / `snore upgrade` - Tool installation/update
- `snore completions` - Shell tab completion management
- `snore logs` - Log file management
- `snore db drop` - Too dangerous for web interface
- `snore serve` - Server startup (is the web UI's prerequisite)

## Purpose

**Problem:**
The web UI has two issues: (1) it looks generic/unpolished due to PrimeVue's Aura theme with minimal customization, and (2) it exposes a tiny fraction of the CLI's capabilities — the primary user workflow (importing data from an SD card) isn't even available in the web UI.

**Value:**
- Modern, polished aesthetic matching the shadcn/ui design language
- Full CLI parity so users can do everything from the browser
- Shared service layer eliminates logic duplication between CLI and API
- Dark mode support

**Requirements:**
- Vue 3 stays (no framework rewrite)
- uPlot stays for charting (framework-agnostic, handles waveform rendering)
- FastAPI + SQLite + SQLAlchemy stay for backend
- Both CLI and API must share the same service layer
- Import must work in a browser context (file upload, not filesystem paths)

**Context:**
Research found the CLI has 30 commands with ~85 options across 8 groups: session (list/show/delete/enable/disable), analysis (run/list/show/delete), db (init/stats/vacuum/drop), waveform (list/show/compare), export (raw/csv/json), rx (history/current/compare), completions (bash/zsh/install/uninstall), logs (path/show/clear), plus standalone commands (setup, upgrade, import, stats, validate, serve).

The frontend is a clean Vue 3 SPA (18 SFCs, 7 views, 10 components, 2 composables) talking to a FastAPI REST API (8 routers, 24 endpoints). The architecture is already decoupled — pure JSON API, OpenAPI type generation via `openapi-typescript`, CORS configured. No server-side rendering.

Several CLI commands bypass the service layer: `import` talks directly to `parser_registry` and `SessionImporter`, batch `analysis run` queries `models.Session`/`models.Day` directly with `ThreadPoolExecutor`, `waveform compare` has ~85 lines of inline event matching logic, and `db vacuum` executes raw SQL. These must be extracted into services before API endpoints can be built.

## Implementation Details

Sequencing rationale: backend first (Phases 1-2), then frontend (Phases 3-7). The backend service extraction and API endpoints are the critical path — every new UI view depends on them. The design system swap is not a blocker; it only affects how views are built, not whether they can be built. Doing all Python work first avoids context-switching between backend and frontend, and means when frontend work begins, all APIs are ready and tested. The design swap (Phases 3-4) then establishes component patterns (TanStack Table wrapper, shadcn-vue conventions) immediately before building new views (Phases 5-6), so every view is built once in its final form.

### Phase 1: Backend Services + CLI Refactoring
   [DONE] Create `ImportService` in `src/snore/services/import_service.py`
      Extract from `src/snore/cli/commands/import_data.py` (406 lines). Methods: `detect_sources(path) -> list[ImportSource]`, `import_sources(sources, force, batch_size, ...) -> ImportResult`, `import_from_upload(uploaded_files, ...) -> ImportResult`. The upload method writes to temp dir then delegates to same pipeline.
   [DONE] Refactor `src/snore/cli/commands/import_data.py` to delegate to `ImportService` -- CLI keeps: interactive source selection prompts (`click.prompt`), Rich progress display, CLI-specific error formatting
   [DONE] Add `run_batch_analysis` to `AnalysisFacade` in `src/snore/services/analysis_facade.py`
      Extract from `src/snore/cli/groups/analysis.py` `_analyze_batch` (queries `models.Session`/`models.Day` directly, uses `ThreadPoolExecutor`). Method: `run_batch_analysis(from_date, to_date, modes, store_results, max_workers, progress_callback) -> BatchAnalysisResult`
   [DONE] Refactor `src/snore/cli/groups/analysis.py` `_analyze_batch` to delegate to `AnalysisFacade.run_batch_analysis`
   [DONE] Add `compare_events` to `WaveformService` in `src/snore/services/waveform_service.py`
      Extract from `src/snore/cli/groups/waveform.py` `compare_events` (~85 lines inline event matching with 5-second tolerance). Method: `compare_events(session_id, mode, tolerance_seconds) -> EventComparisonResult`
   [DONE] Refactor `src/snore/cli/groups/waveform.py` `compare_events` to delegate to `WaveformService.compare_events`
   [DONE] Add `vacuum` method to `DatabaseService` in `src/snore/services/database_service.py`
      Extract from `src/snore/cli/groups/db.py` (raw `session.execute(text("VACUUM"))`)
   [DONE] Refactor `src/snore/cli/groups/db.py` vacuum command to delegate to `DatabaseService.vacuum`
   [DONE] Add Pydantic schemas in `src/snore/services/schemas.py`: `ImportSource`, `ImportResult`, `BatchAnalysisResult`, `EventComparisonResult`, `EventComparisonDetail`, `ValidationReportResponse`
      Note: added 8 schemas total — `ImportSource`, `ImportSourceResult`, `ImportResult`, `BatchSessionResult`, `BatchAnalysisResult`, `EventComparisonDetail`, `EventComparisonResult`, `VacuumResult`. `ValidationReportResponse` was not needed as `ValidationReport` from `snore.validation` is already a proper Pydantic model used directly by the router.

### Phase 2: New API Endpoints (depends on: Phase 1)
   [DONE] Create import router `src/snore/api/routers/import_data.py`
      `POST /api/v1/import/detect` -- accepts `{"path": "/mnt/sd-card"}`, returns detected `ImportSource` list (for localhost use)
      `POST /api/v1/import` -- multipart file upload, delegates to `ImportService.import_from_upload`
      Note: requires `python-multipart` dependency (added to `pyproject.toml`)
   [DONE] Create export router `src/snore/api/routers/export.py`
      `GET /api/v1/export/csv` -- query params (date range, device), returns `StreamingResponse` zip
      `GET /api/v1/export/json` -- query params (date range, device), returns JSON file
      `GET /api/v1/export/raw` -- query params (date range, device), returns `StreamingResponse` zip
      All delegate to existing `ExportService` methods, use `tempfile.TemporaryDirectory` for cleanup
   [DONE] Add batch analysis endpoint to `src/snore/api/routers/analysis.py`
      `POST /api/v1/analysis/batch` -- body: `{"from_date", "to_date", "modes"}`, delegates to `AnalysisFacade.run_batch_analysis`
   [DONE] Create DB router `src/snore/api/routers/db.py`
      `GET /api/v1/db/stats` -- delegates to `DatabaseService.get_stats`
      `POST /api/v1/db/vacuum` -- delegates to `DatabaseService.vacuum`
   [DONE] Create validation router `src/snore/api/routers/validation.py`
      `POST /api/v1/validate` -- body: `{"from_date", "to_date", "mode"}`, delegates to `BatchValidator.validate_date_range`
      Note: `BatchValidator` takes `(db_session, profile=None)` so cannot use `service_dep()` — injects `db: Session = Depends(get_db)` directly
   [DONE] Add waveform compare endpoint to `src/snore/api/routers/waveforms.py`
      `GET /api/v1/sessions/{session_id}/waveforms/compare` -- query param: `mode` (default `aasm`), delegates to `WaveformService.compare_events`
      Note: this endpoint is defined BEFORE `/{session_id}/waveforms/{waveform_type}` in the router — FastAPI would otherwise match "compare" as a `waveform_type` path parameter
      Note: `show_unmatched` param was not implemented at API layer (display filtering stays CLI-only)
   [DONE] Expand delete-preview in `src/snore/api/routers/sessions.py`
      `POST /api/v1/sessions/delete-preview` -- body accepts `session_ids`, `device`, `from_date`, `to_date`, `delete_all` (matches CLI flexibility)
      Added `BulkDeletePreviewRequest` model to `src/snore/api/schemas.py`
   [DONE] Add `response_model` annotations to untyped endpoints: `GET /stats/trends`, `GET /stats/records`, `GET/POST /sessions/{id}/analysis` -- enables 100% OpenAPI type coverage
      Note: `/stats/trends` and `/stats/records` annotated with `response_model=dict[str, list[list[Any]]]` and `response_model=dict[str, dict[str, list[list[Any]]]]` respectively (tuples serialize as JSON arrays). `/sessions/{id}/analysis` endpoints left untyped — `AnalysisResult` is a complex internal model whose schema varies by mode; a comment was added explaining the intentional omission.
   [DONE] Register all new routers in `src/snore/api/app.py` following existing pattern
   [DONE] Run `just ui-generate-types` to regenerate `ui/src/types/generated.ts`
      Run after Phase 2 PR is merged to main so the dev server picks up new endpoints. Command: `just ui-generate-types`
      Done: ran in session 4 after PR #86 merged; result committed directly to main (`00c5b7d`).

### Phase 3: Design System Foundation -- Tailwind CSS v4 + shadcn-vue Setup
   [DONE] Install Tailwind CSS v4 with `@tailwindcss/vite` plugin, configure in `ui/vite.config.ts`
      `@tailwindcss/vite` added as devDep; plugin placed before `vue()` in vite.config.ts (order matters).
   [DONE] Install shadcn-vue prerequisites: `class-variance-authority`, `clsx`, `tailwind-merge`, `@lucide/vue`, `reka-ui`
      Note: `lucide-vue-next` is deprecated — use `@lucide/vue` instead. shadcn-vue init also installs `tw-animate-css` and `shadcn-vue` as additional deps.
   [DONE] Initialize shadcn-vue: create `ui/components.json`, run `npx shadcn-vue@latest init`
      Init gotchas (see detailed notes in Gotchas section): requires `@import 'tailwindcss'` in CSS file and `paths` key in ROOT `tsconfig.json`. `framework` key is NOT valid in `components.json` for this version.
      Style: New York, Base color: Zinc. Init rewrites tailwind.css with full config including Geist font, tw-animate-css, and oklch-based CSS custom properties.
   [DONE] Install foundational shadcn-vue components via `npx shadcn-vue@latest add`
      Installed: `button`, `badge`, `card`, `dialog`, `alert-dialog`, `select`, `toggle`, `toggle-group`, `collapsible`, `popover`, `calendar`, `table`, `dropdown-menu`, `separator`
      All 108 SFC files copied to `ui/src/components/ui/`. These are source files, not dependencies.
   [DONE] Create `ui/src/lib/utils.ts` with the `cn()` helper (clsx + tailwind-merge)
      Generated by shadcn-vue init. Note: `ui/src/lib/` is in root `.gitignore` (`lib/`) — must force-add with `git add -f`.
   [DONE] Define design tokens in `ui/src/assets/tailwind.css`
      shadcn-vue init rewrites this file with `@import 'tailwindcss'` (full, includes preflight), `@import "tw-animate-css"`, `@import "shadcn-vue/tailwind.css"`, `@custom-variant dark (&:is(.dark *))`, and full `@theme` + `:root` + `.dark` blocks in oklch color space. Prettier formatting required for CI.
   [DONE] Create `ui/src/composables/useDarkMode.ts` -- toggles `dark` class on `<html>`
      Singleton `isDark` ref, module-scope `watchEffect` applies `.dark` to `<html>`, `localStorage` persistence. `initDarkMode()` exported for pre-mount call — applies the class directly to avoid FOUC. The `watchEffect` must be at module scope (not inside `useDarkMode()`) to prevent duplicate watchers when multiple components call the composable.
   [DONE] Update `ui/src/main.ts` to import new CSS; PrimeVue removed immediately (coexistence not needed)
      Since both phases done in one session, PrimeVue was removed from main.ts at the start of Phase 4 work rather than coexisting.
   [DONE] Verify `npm run build && npm run type-check` pass
      Both pass. ESLint config updated with `vue/multi-word-component-names: off` override for `src/components/ui/**/*.vue` (shadcn-vue has single-word component names like `Toggle`, `Badge`). `CalendarHeading.vue` had `any` type in slot definition — changed to `unknown`.

### Phase 4: Design System Migration -- Replace PrimeVue (depends on: Phase 3)
   [DONE] Replace `Button` (5 files) -- shadcn Button with `variant`/`size`; icons as child components from `@lucide/vue`
      Mapping: `severity="secondary"` → `variant="outline"`, `severity="danger"` → `variant="destructive"`, `text rounded` (icon-only) → `variant="ghost" size="icon"`, `:loading` → `:disabled` + conditional `<Loader2 class="animate-spin" />`, `size="small"` → `size="sm"`.
      Files: `SessionListView`, `AnalysisView`, `DeleteConfirmDialog`, `MultiWaveformView`, `WaveformToolbar`
   [DONE] Replace `DataTable` + `Column` (6 files) -- shadcn Table parts with manual `v-for` per row; no shared TanStack wrapper
      Decision: each DataTable migrated inline rather than building a shared wrapper — the use cases varied enough (lazy server-side, client-side paginated, simple static) that a generic wrapper would have been more complex than the inline replacements.
      Client-side pagination: `currentPage` ref + `computed` slice + prev/next Button controls. Resets page on filter change via `watch`.
      Server-side pagination (SessionListView): kept existing `fetchPage(offset)` + `offset`/`totalRecords` refs; replaced PrimeVue paginator with explicit prev/next Buttons + page indicator.
      Row class binding (`RxHistoryView` best/worst rows): `:class="rowClass(row)"` directly on `<TableRow>` — no `:deep()` CSS needed.
      Files: `SessionListView`, `EventExplorerView`, `RxHistoryView`, `PeriodStatsTable`, `AnalysisView`, `DashboardView`
   [DONE] Replace `Tag` with `Badge` (5 files) -- severity mapped to variant; interactive chips styled as `<button>` elements
      Severity mapping: `success` → `class="bg-green-100 text-green-800 dark:..."`, `danger` → `variant="destructive"`, `info`/`secondary` → `variant="secondary"`.
      Special case (`EventExplorerView`): `Tag` used with `@click` as interactive filter chips. Replaced with styled `<button>` elements using `bg-primary`/`bg-muted/50` active/inactive classes — shadcn Badge is display-only and has no click handling.
      Files: `DashboardView`, `SessionListView`, `SessionDetailView`, `EventExplorerView`, `RxHistoryView`
   [DONE] Replace `DatePicker` (1 file) -- native `<input type="date">` with Tailwind styling
      Decision: used native `<input type="date">` instead of shadcn Calendar+Popover. shadcn Calendar uses reka-ui's `DateValue` type from `@internationalized/date` (not JS `Date`), requiring conversion. Native date input avoids that dependency and is functionally equivalent for YYYY-MM-DD selection.
      File: `SessionListView`
   [DONE] Replace `Select` (3 files) -- shadcn Select with `SelectTrigger`/`SelectValue`/`SelectContent`/`SelectItem v-for`
      Files: `SessionListView`, `MultiWaveformView`, `WaveformToolbar`
   [DONE] Replace `ToggleButton`/`SelectButton` with `Toggle`/`ToggleGroup` (4 files)
      `ToggleButton` → shadcn `Toggle` with `:model-value`/`@update:model-value` (NOT `v-model:pressed` — `pressed` is a slot prop, not a component prop).
      `SelectButton` → `ToggleGroup type="single" variant="outline"` with `ToggleGroupItem v-for`. Must guard against deselection: `@update:model-value="(v) => { if (v) ref = v as string }"` — reka-ui emits `undefined` when clicking an already-selected item.
      Files: `SessionListView` (Toggle), `WaveformToolbar` (Toggle), `AnalysisView` (ToggleGroup), `StatsView` (ToggleGroup)
   [DONE] Replace `Dialog` (1 file) -- shadcn `AlertDialog` with manual close control
      `<Dialog :visible modal>` → `<AlertDialog :open>`. `<template #footer>` → `<AlertDialogFooter>`.
      Critical: `AlertDialogAction` wraps reka-ui `DialogClose` — using it auto-dismisses the dialog before async operations complete. Use a plain `Button` for the action and control close via `@update:open` on the parent. Also use `AlertDialogDescription` (not plain `<div>`) for the body text so screen readers auto-announce it.
      File: `DeleteConfirmDialog`
   [DONE] Replace `Panel` with `Collapsible` (1 file)
      `<Panel :toggleable :collapsed>` → `<Collapsible v-model:open="settingsOpen">` with `<CollapsibleTrigger>` + `ChevronDown` rotate animation + `<CollapsibleContent>`. Added `const settingsOpen = ref(false)`.
      File: `SessionDetailView`
   [DONE] Migrate global CSS -- all `var(--p-*)` refs replaced with `var(--color-*)` tokens or Tailwind utilities
      `App.vue` body rules deleted entirely (tailwind.css `@layer base` now sets `bg-background text-foreground font-sans`).
      `layout.css` updated: `--p-surface-card` → `var(--color-card)`, `--p-surface-border` → `var(--color-border)`, `--p-text-muted-color` → `var(--color-muted-foreground)`, `--p-red-500` → `var(--color-destructive)`, `--p-primary-color` → `var(--color-primary)`.
      Scoped styles in views/components: converted to Tailwind utility classes inline or `var(--color-*)`.
   [DONE] Replace all `pi pi-*` icon classes with `@lucide/vue` components across all views
      21 unique icons replaced. Spinner: `<Loader2 class="h-4 w-4 animate-spin" />`.
      Full mapping: `pi-arrow-left`→`ArrowLeft`, `pi-ban`→`Ban`, `pi-chart-bar`→`BarChart3`, `pi-chart-line`→`TrendingUp`, `pi-check`→`Check`, `pi-cog`→`Settings`, `pi-exclamation-triangle`→`AlertTriangle`, `pi-eye`→`Eye`, `pi-eye-slash`→`EyeOff`, `pi-filter-slash`→`FilterX`, `pi-info-circle`→`Info`, `pi-list`→`List`, `pi-play`→`Play`, `pi-plus`→`Plus`, `pi-search-minus`→`ZoomOut`, `pi-spin pi-spinner`→`Loader2 animate-spin`, `pi-stop`→`Square`, `pi-th-large`→`LayoutGrid`, `pi-times`→`X`, `pi-trash`→`Trash2`, `pi-chart-bar`→`BarChart3`
   [DONE] Remove PrimeVue -- `primevue`, `@primevue/themes`, `primeicons` removed from `package.json` and `main.ts`
      `npm uninstall primevue @primevue/themes primeicons` confirmed empty in `npm ls primevue`.
   [DONE] Add dark mode toggle to `AppSidebar` using `useDarkMode` composable
      `Sun`/`Moon` icons in a `sidebar-footer` div at bottom of sidebar; `const { isDark, toggleDark } = useDarkMode()`.
   [DONE] Fix `RxHistoryView.vue` template bug -- `v-else-if="error"` after `v-else` makes error display unreachable
      Fixed conditional order to: `v-if="loading"` → `v-else-if="error"` → `v-else-if="history.length"` → `v-else` (empty state).

### Phase 5: High-Impact UI Features (depends on: Phases 2 + 4)

All new views follow established patterns (see "Existing Frontend Patterns" in Gotchas below).

   [DONE] Build Import View `ui/src/views/ImportView.vue` -- multi-step form:
      Step 1: Source selection — `<input type="file" webkitdirectory>` for directory upload with drag-and-drop zone, OR text input + "Detect Sources" button for filesystem path when on localhost (detect via `window.location.hostname`). Next enabled when sources detected OR files selected.
      Step 2: Options — force re-import Toggle, sort order ToggleGroup (`date-asc`/`date-desc`/`filesystem`). Date range and limit omitted for simplicity.
      Step 3: Progress — Axios `onUploadProgress` drives upload progress bar (0-100%); after upload, "Processing..." spinner (API is synchronous — returns ImportResult when done).
      Step 4: Results — ImportResult StatCards (imported/skipped/failed), per-source breakdown cards, warnings list, "View Sessions" + "Import More" buttons.
      Files: `ui/src/api/import.ts` — `detectSources` via `apiPost`, `importFiles` via raw `axios.post` with `FormData` + `onUploadProgress`. Route + sidebar link added.
      Note: 500 MB upload limit enforced by backend; UI shows warning if selected files exceed it.
   [DONE] Build Export View `ui/src/views/ExportView.vue`
      Format selector: ToggleGroup (Raw/CSV/JSON, deselection guard applied). Date range: native date inputs. Device: Select from `getDevices()`.
      Format-specific options: CSV has "Include waveforms" Toggle; Raw has "Trim STR.edf to date range" Toggle (only enabled when both dates set). JSON has none.
      Download: raw `api.get` with `{ responseType: 'blob' }` (cannot use `apiGet` wrapper — it expects JSON). `downloadBlob(blob, filename)` helper triggers download via `URL.createObjectURL` + temp `<a>`. Success state shows file size.
      File: `ui/src/api/export.ts` — `exportCsv`, `exportJson`, `exportRaw`, `downloadBlob`. Route + sidebar link added.
   [DONE] Enhance `DashboardView.vue` -- added second StatCard row (SpO₂, Pulse, Pressure, Resp Rate), event breakdown badge section using `EVENT_COLORS`, SpO₂ and Leak datasets in trend chart. Calendar heatmap `@day-click` now navigates to `day-detail` route instead of filtered sessions list.
   [DONE] Enhance `SessionDetailView.vue` -- replaced flat 6-card grid with 5 collapsible sections showing all 32 SessionStatistics fields:
      **Respiratory Events** (default open): AHI/REI/OAI/CAI/HI indices + obstructive/central/mixed apneas, hypopneas, RERAs, flow limitations counts
      **Pressure**: pressure mean/min/max/95th, EPAP mean/min/max/95th (cmH₂O)
      **Leak**: leak mean/70th/95th (L/min)
      **Oximetry**: SpO₂ mean/min/time_below_90, pulse mean/min/max
      **Respiratory**: respiratory rate (br/min), tidal volume (mL), minute ventilation (L/min)
      Each section uses Collapsible/CollapsibleTrigger/CollapsibleContent (already imported). Refs: `respiratoryOpen = ref(true)`, others start `false`.

### Phase 6: Remaining UI Features (depends on: Phase 2)

   [DONE] Build Analysis Management View `ui/src/views/AnalysisManagementView.vue`
      Filter bar: from/to date inputs + "Analyzed Only" Toggle + "Run Batch" button.
      Table: Session Date (RouterLink to `/sessions/:id/analysis`), Duration, Analyzed/Not Analyzed Badge, delete button (only when `has_analysis`). Server-side pagination via `getAnalysisSessions`.
      Batch analysis: AlertDialog with from/to dates + AASM/ResMed ToggleGroup; calls `runBatchAnalysis`; shows BatchAnalysisResult StatCards (total/successful/failed) after close.
      Delete: `DeleteConfirmDialog` with `getAnalysisDeletePreview` showing records + patterns count.
      New API: `runBatchAnalysis` added to `ui/src/api/analysis.ts`. Route + sidebar link added.
   [DONE] Build Database Management View `ui/src/views/DatabaseView.vue`
      Data loading: `useApiLoad(() => getDbStats())`. `DatabaseStatsPublic` excludes `db_path` (intentionally omitted from API for security).
      Layout: overview card (size_mb, first/last session, device/profile count), 6-item StatCard grid (sessions/days/events/waveforms/analysis/patterns), coverage section with percentage badges for waveform/event/analysis coverage.
      Vacuum: plain Button (not AlertDialogAction) triggers confirmation; calls `vacuumDb()`; shows VacuumResult (size_before → size_after with savings); reloads stats after success.
      New: `ui/src/api/db.ts` — `getDbStats`, `vacuumDb`. Route added.
   [DONE] Build Validation View `ui/src/views/ValidationView.vue`
      Form: from/to date inputs (required) + mode Select (`aasm`/`aasm_relaxed`/`resmed`, default `aasm`) + "Run Validation" button. Form shown when `result` is null; results shown otherwise. "Run Again" resets to null.
      Results: 5 StatCards (avg apnea/hypopnea sensitivity/F1, sessions validated — computed as `value * 100` for percentage display). Per-session table with low-sensitivity row highlighting using CSS specificity gotcha guard: `:class="{ 'bg-amber-50 dark:bg-amber-950/30': isLow, 'even:bg-muted/50': !isLow }"`.
      New: `ui/src/api/validation.ts` — `runValidation`. Route added.
   [DONE] Build Day Detail View `ui/src/views/DayDetailView.vue`
      Props: `dayDate: string` from route. Data loading: `useApiLoad(() => getDay(props.dayDate))`.
      Layout: back link to Dashboard, date header via `formatDateWithWeekday`, summary StatCards (total_therapy_hours, ahi, session_count, oai/cai/hi), additional stats row when available (avg_pressure, avg_leak, avg_spo2), sessions table with session_ids as RouterLinks to `/sessions/:id`.
      Note: `DayDetail.session_ids` is optional — guard with `?.length`.
      Route: `{ path: '/days/:date', name: 'day-detail', props: (route) => ({ dayDate: ... }) }`. DashboardView `@day-click` updated to navigate to `day-detail` params.
   [DONE] Add waveform compare section to `AnalysisView.vue`
      Section added below CSR/Periodic Breathing block, guarded by `v-if="comparison"`. Fetched in `onMounted` in a separate `try/catch` (silent failure — section simply doesn't render if unavailable).
      Layout: 4 summary StatCards (machine events, programmatic events, false negatives, false positives). Two tables: False Negatives and False Positives (merged from `false_positives_apnea` + `false_positives_hypopnea`, sorted by `start_time`). Columns: event type badge, time offset (RouterLink to `session-detail?t={start_time}`), duration, confidence, flow reduction.
      New API: `getWaveformCompare` added to `ui/src/api/waveforms.ts`. `EventComparisonResult` type re-exported from `types/index.ts`.
   [DONE] Add multi-select + bulk delete to `SessionListView.vue`
      `selectedIds: ref<Set<number>>(new Set())`. Checkbox column added as first column (with select-all header checkbox, colspan updated from 6 to 7).
      "Delete Selected" destructive Button shown in filter bar when `selectedIds.size > 0`. Bulk delete uses same `DeleteConfirmDialog` as single delete: `deleteTargetId = null` signals bulk mode to `executeDelete`. Selection cleared after successful delete and on filter change (flows through `fetchPage(0)`).
      New API: `getBulkDeletePreview` added to `ui/src/api/sessions.ts`.

### Phase 7: Polish
   [DONE] Enable column sorting on SessionListView — `sortBy` ref drives existing backend `sort_by` param (`date-asc`/`date-desc`/`duration`). Clickable `<TableHead>` cells with `ArrowUp`/`ArrowDown`/`ArrowUpDown` indicators. `toggleSort()` cycles date asc/desc and toggles duration on/off. No TanStack Table needed — manual ref is simpler for server-side sorting.
   [DONE] uPlot dark mode — `watch(isDark, () => createChart())` in `WaveformChart.vue` and `TrendChart.vue`. Dark palette: axis `#a1a1aa`, grid `#27272a`, series `#60a5fa` (blue-400). `chartColors()`/`axisColors()` helpers branch on `isDark.value`. Must destroy+recreate (no `setOptions()` API in uPlot).
   [DONE] Responsive sidebar — `hidden md:flex` on desktop `<AppSidebar>`. Hamburger button (`Menu` icon, fixed top-left, `md:hidden`) opens `<Sheet v-model:open="mobileMenuOpen">` containing a second `<AppSidebar>`. `watch(() => route.path, ...)` auto-closes Sheet on navigation. `SheetTitle class="sr-only"` required for a11y. CSS: `@media (max-width: 767px) { .app-layout { grid-template-columns: 1fr } .app-main { padding-top: 3.5rem } }`. Adds shadcn-vue `Sheet` component.
   [DONE] Skeleton loading states — Adds shadcn-vue `Skeleton` component. Dashboard: two 4-card skeleton rows + chart + calendar placeholders before real content. SessionListView: 8 skeleton table rows with per-column widths. SessionDetailView: skeleton back link + heading + meta pills + waveform area + 8 stat card skeletons using `.session-detail` class for consistent max-width.
   [DONE] Restructure sidebar navigation: Data (Dashboard, Sessions), Analysis (Analysis, Validation), Tools (Import, Export, Database), Settings (Stats, RX History)
      Pulled forward into Phase 5 shared infrastructure. `AppSidebar.vue` restructured with `.nav-group-label` CSS class for section headers. Note: "Days" omitted from sidebar — DayDetailView is a parameterized route accessed via calendar heatmap, not a standalone list.
   [DONE] OpenAPI type audit — Added `response_model=AnalysisResult` to `GET`/`POST /sessions/{id}/analysis`. Fixed `AnalysisFacade.run_analysis` and `get_analysis_result` return types (were `Any` despite `AnalysisService` being fully typed). Regenerated types. Replaced ~70 lines of hand-maintained interfaces (`ApneaEvent`, `HypopneaEvent`, `RERAEvent`, `AnalysisEvent`, `ModeResult`, `AnalysisResult`) with 6 generated `Schemas['...']` aliases. `TrendData`/`RecordsData` kept hand-maintained — backend's `dict[str, list[list[Any]]]` generates as `{ [key: string]: unknown[][] }`, less useful than the typed versions.

## Gotchas & Issues Encountered

### Existing Frontend Patterns (reference for new views)
New views and API modules must follow these established patterns. An agent building new views should read these files as templates rather than inventing new patterns.

**API client pattern** (`ui/src/api/client.ts`):
All API modules use the `createApiEndpoint` factory or its convenience wrappers (`apiGet`, `apiPost`, `apiPatch`, `apiDelete`, `apiGetOrNull`). Example from `api/sessions.ts`:
```typescript
export const getSessions = apiGet<PaginatedResponse<SessionListItem>, [params?: SessionsParams]>(
    '/sessions/', (params = {}) => ({ params })
)
export const getSession = apiGet<SessionDetail, [id: number, includeSettings?: boolean]>(
    (id) => `/sessions/${id}`,
    (_id, includeSettings = true) => ({ params: { include_settings: includeSettings } }),
)
```
New API modules (`import.ts`, `export.ts`, `db.ts`, `validation.ts`) follow this same pattern. Import types from `@/types`. Exception: file uploads use raw `axios.post` with `FormData` for `onUploadProgress` support.

**Data loading pattern** (`ui/src/composables/useApiLoad.ts`):
Views use `useApiLoad(fetcher)` which auto-fetches on mount and provides `{ data, loading, error, reload }`. For parallel fetches, use `Promise.all` inside the fetcher (see `DashboardView.vue` lines 87-95 for the pattern). For manual re-fetching on filter changes, call `reload()` from a `watch`.

**Route registration** (`ui/src/router/index.ts`):
Routes use lazy loading: `component: () => import('@/views/FooView.vue')`. Routes with ID params use a props function: `props: (route) => ({ sessionId: Number(route.params.id) })`. Add new routes to the `routes` array.

**Sidebar navigation** (`ui/src/components/AppSidebar.vue`):
Nav links are `<RouterLink to="/path" class="nav-item">` with a `<i class="pi pi-icon-name" />` icon (will be `lucide-vue-next` after Phase 4) and `<span>Label</span>`. Active state is handled by vue-router's `.router-link-active` class. Add new links in the `<nav class="sidebar-nav">` section.

**Type exports** (`ui/src/types/index.ts`):
Re-export generated types from `./generated.ts` as `type Schemas = components['schemas']` then `export type Foo = Schemas['Foo']`. Hand-written types go below for endpoints without `response_model` (though Phase 2 eliminates most of these).

### shadcn-vue Init Requirements
**Problem:** `npx shadcn-vue@latest init` fails if the CSS file doesn't contain `@import 'tailwindcss'` (the full shorthand), and fails if the root `tsconfig.json` doesn't have a `paths` key (it only reads the root `tsconfig.json`, not project references like `tsconfig.app.json`).
**Solution:** Before running init, put `@import 'tailwindcss'` in the CSS file and add `"compilerOptions": { "paths": { "@/*": ["./src/*"] } }` to the root `tsconfig.json`. Init will rewrite the CSS file with its own full config (oklab color tokens, `@layer base`, Geist font import, `tw-animate-css`). The `paths` entry in root tsconfig is additive — existing `references` array is preserved.
**Impact:** `framework` key is NOT valid in `components.json` (verified against schema). Omit it. Component scanning needs `@import 'tailwindcss'` not the granular `tailwindcss/theme` + `tailwindcss/utilities` imports.

### Tailwind CSS v4 Preflight Included
**Problem:** The shadcn-vue init generates `@import 'tailwindcss'` which includes preflight (a CSS reset that zeroes headings, buttons, links). This is incompatible with PrimeVue Aura.
**Solution:** Since both Phases 3+4 were completed in the same session, PrimeVue was removed immediately rather than coexisting. If a future session needs to reinstall PrimeVue temporarily, use `@import 'tailwindcss/theme'; @import 'tailwindcss/utilities'` (no preflight) — but note shadcn-vue init will overwrite this.
**Impact:** The tailwind.css `@layer base` block sets `body { @apply bg-background text-foreground font-sans }` — the body rules in `App.vue` can be deleted.

### No TanStack Table Wrapper Built
**Problem:** The original plan called for a reusable TanStack Table wrapper at `ui/src/components/ui/data-table.vue`.
**Decision:** Skipped — each DataTable usage was different enough (server-side lazy, client-side paginated, static, row-click navigation, row-class conditional) that a generic wrapper would have been more complex than inline replacements. The shadcn Table parts (`Table`, `TableHeader`, `TableRow`, `TableHead`, `TableBody`, `TableCell`) are already low-ceremony enough that inline usage is clean.
**Impact:** `SessionListView.vue` (server-side pagination) manages its own `offset`/`totalRecords` state and calls `fetchPage(offset)`. Client-side pagination uses `currentPage` ref + `computed` slice.

### DatePicker → Native Input
**Problem:** shadcn-vue's `Calendar` component uses reka-ui's `DateValue` type from `@internationalized/date` (not JS `Date`). Bridging between the two types requires conversion utilities.
**Decision:** Used native `<input type="date">` with Tailwind styling instead. Functionally identical for YYYY-MM-DD date selection and avoids the `@internationalized/date` dependency.
**Impact:** `fromDate`/`toDate` refs in `SessionListView.vue` are still `Date | null`. The input's `@input` handler converts the string value with `new Date(value + 'T00:00:00')`.

### lucide-vue-next Deprecated
**Problem:** `lucide-vue-next` is deprecated (shows npm warning on install).
**Solution:** Use `@lucide/vue` — same API, all the same icons, actively maintained. Named exports are identical: `import { Loader2, ArrowLeft, ... } from '@lucide/vue'`.
**Impact:** Any documentation or shadcn-vue guides referencing `lucide-vue-next` should be read as `@lucide/vue`.

### ESLint Multi-Word Component Name Rule
**Problem:** shadcn-vue ships single-word component files (`Toggle.vue`, `Badge.vue`, `Select.vue`) which fail the `vue/multi-word-component-names` ESLint rule that prevents naming conflicts with HTML elements.
**Solution:** Added an ESLint config override in `eslint.config.js` targeting `src/components/ui/**/*.vue` with `vue/multi-word-component-names: off`. App components (in `src/components/*.vue`) still enforce the rule.

### lib/ in Root .gitignore
**Problem:** The root `.gitignore` contains `lib/` which matches `ui/src/lib/` — `git add` silently ignores this directory.
**Solution:** Use `git add -f ui/src/lib/` to force-add it. This is application source code (`cn()` utility), not a build artifact.

### Prettier Formatting for tailwind.css in CI
**Problem:** shadcn-vue init generates `tailwind.css` with inconsistent indentation (mix of 2-space and 4-space blocks). The CI `ui-format-check` step (prettier) fails.
**Solution:** Run `npx prettier --write src/assets/tailwind.css` after init. Do this before committing.

### Import in Browser Context
**Problem:** The CLI uses filesystem paths (`snore import /mnt/sd-card`) which don't work in a browser.
**Solution:** Two-tier approach: (1) `<input type="file" webkitdirectory>` for directory upload via multipart POST to `POST /api/v1/import`, (2) path-based `POST /api/v1/import/detect` when API runs on localhost. Both converge on the same `ImportService` pipeline. SD card data directories are typically 50-200 MB, making upload feasible.
**Impact:** Import View must detect localhost vs remote and show the appropriate input method.

### PrimeVue DataTable Lazy Pagination
**Problem:** `SessionListView` uses PrimeVue's `lazy` pagination mode (`lazy`, `@page` event) for server-side pagination. TanStack Table has a different API.
**Solution:** Build a reusable `DataTable` wrapper component that supports the same pattern: parent controls data fetching and passes `totalRecords`, wrapper handles UI state.
**Impact:** The wrapper is the foundation for all 5+ tables across the app.

### Untyped API Endpoints
**Problem:** Three endpoints return `Any` instead of typed Pydantic models: `GET /stats/trends`, `GET /stats/records`, `GET/POST /sessions/{id}/analysis`. The Vue frontend maintains hand-written TypeScript types for these.
**Solution:** Add `response_model` annotations to these endpoints so `openapi-typescript` generates types for 100% of the API surface. Do this in Phase 2 alongside new endpoints.
**Impact:** Without this, hand-maintained types in `ui/src/types/index.ts` will drift from the backend.

### reka-ui AlertDialogAction Wraps DialogClose
**Problem:** `AlertDialogAction` in reka-ui renders as `DialogClose` — clicking it synchronously dismisses the dialog before any `@click` handler runs. This means async operations (like deleting a session) appear to complete instantly with no loading feedback, and errors surface in the parent view instead of the dialog.
**Solution:** Never use `AlertDialogAction` for async actions. Use a plain `Button` inside `AlertDialogFooter` and control dialog visibility manually via the parent's `v-model:visible` after the async operation completes. Also remove explicit `@click` from `AlertDialogCancel` — reka-ui's built-in `DialogClose` behavior already emits `update:open(false)` which propagates through `@update:open`.
**Impact:** Using `AlertDialogAction` (even with `as-child`) makes loading spinners, disabled states, and error handling dead code.

### reka-ui Toggle Props: modelValue Not pressed
**Problem:** `Toggle`'s controlled prop is `modelValue` (emits `update:modelValue`), not `pressed`. The `pressed` identifier exists only as a slot prop for the default slot. Binding `:pressed="value"` sets an HTML attribute, not the component's reactive state; listening to `@update:pressed` never fires.
**Solution:** Always use `:model-value` and `@update:model-value` for controlled Toggle state.
**Impact:** Mis-wiring makes the toggle non-functional — clicks have no effect on parent state.

### reka-ui ToggleGroup type="single" Allows Deselection
**Problem:** Unlike PrimeVue's `SelectButton`, reka-ui's `ToggleGroup type="single"` allows deselecting the active item by clicking it again. The emitted value is `undefined`, which can crash API calls or silently hide UI sections.
**Solution:** Replace `v-model` with explicit `:model-value` + `@update:model-value` handler that guards against falsy values: `(v) => { if (v) ref = v as string }`.
**Impact:** Without the guard, clicking the active toggle sends `undefined` as an API parameter or causes computed properties to return null.

### CSS Specificity: even:bg-muted/50 vs Dynamic Classes
**Problem:** Tailwind's `even:bg-muted/50` compiles to `.even\:bg-muted\/50:nth-child(even)` with specificity 0-2-0 (class + pseudo-class). Dynamic conditional classes like `bg-green-500/10` are plain class selectors (0-1-0). The even-row stripe always wins on even-indexed rows, making the dynamic highlight invisible.
**Solution:** Don't mix unconditional `even:` class with conditional `:class` bindings on the same element. Instead, put `even:bg-muted/50` inside the `:class` object as a conditional that only applies when the dynamic styles are absent: `:class="{ 'bg-green-500/10': isSpecial, 'even:bg-muted/50': !isSpecial }"`.
**Impact:** Visual-only but confusing — best/worst indicators are invisible on half the rows.

### formatIso Timezone Bug with toISOString
**Problem:** `Date.toISOString()` converts to UTC. When a `Date` is created from a local-midnight string (`new Date('2024-01-15T00:00:00')`), `toISOString().slice(0, 10)` returns the previous day for users in UTC+ timezones (e.g., UTC+5 → 2024-01-14 instead of 2024-01-15).
**Solution:** Use local date parts: `d.getFullYear()` / `d.getMonth() + 1` / `d.getDate()` with zero-padding.
**Impact:** Date filter inputs display the wrong date and send the wrong date to the API for roughly half the world's timezones.

### useApiLoad Race Condition
**Problem:** `reload()` has no staleness guard. Rapid calls (e.g., toggling filters quickly) fire parallel requests. The last response to arrive writes `data.value`, which may not be the last request sent.
**Solution:** Sequence counter (`let seq = 0`; capture `++seq` before await; guard all state writes with `if (thisSeq === seq)`). Stale responses are silently dropped.
**Impact:** Users may see data for a previous filter selection after quickly switching filters.

### AlertDialogDescription Required for Screen Readers
**Problem:** reka-ui's `AlertDialogContent` wires `aria-describedby` to the first `AlertDialogDescription` descendant. If the dialog body uses a plain `<div>` or `<p>` instead, screen readers announce the title but skip the body text entirely.
**Solution:** Always wrap dialog body content in `AlertDialogDescription`. Use `as-template` if you need a custom container element.
**Impact:** Accessibility regression — confirmation messages are not auto-announced.

### RxHistory Template Bug
**Problem:** In `RxHistoryView.vue`, `v-else-if="error"` appears after a `v-else` block, making error display structurally unreachable when `!history.length` is true.
**Solution:** Fix template ordering during Phase 4 design system migration.
**Impact:** Error states for RX history are silently swallowed.

### ToggleGroup @update:model-value Typed as AcceptableValue, Not String
**Problem:** reka-ui's `ToggleGroup` emits `AcceptableValue` (a union: `string | number | bigint | Record<string, any> | AcceptableValue[]`), not `string`. Writing `(v) => { if (v) ref = v }` fails TypeScript with error TS2322: "Type 'AcceptableValue' is not assignable to type 'string'".
**Solution:** Cast explicitly: `(v) => { if (v) ref = v as string }`. The `if (v)` guard (deselection protection) is still required.
**Impact:** Missing the cast produces a type error during `just ui-type-check`. Both the guard AND the cast are required.

### StatCard Props: label Not title, value Must Be Number
**Problem:** `StatCard` accepts `label: string`, `value: number | null | undefined`, `unit?: string`, `decimals?: number`. Passing `:value="someString"` (e.g., `(pct * 100).toFixed(1) + '%'`) causes TS2322. Passing `title=` instead of `label=` is silently ignored (attribute, not prop).
**Solution:** Always pass `:value` as a raw number. For percentages, pass `:value="pct * 100"` with `unit="%"` and `:decimals="1"`. Never format the value before passing it.
**Impact:** Wrong prop name or string value causes type errors and blank display.

### Export Blob Endpoints Cannot Use apiGet Wrapper
**Problem:** `apiGet` calls `api.request<T>({ ... })` and returns `response.data` — this works for JSON but for blob responses returns `data` as a parsed JSON attempt on binary content.
**Solution:** Use raw `api.get<Blob>(path, { params, responseType: 'blob' })` and destructure `{ data }`. The `downloadBlob(blob, filename)` helper then triggers browser download via `URL.createObjectURL` + temp `<a>`.
**Impact:** Using `apiGet` for blob endpoints returns corrupted/empty data.

### ExportService Already Exists
**Problem:** The `ExportService` in `src/snore/services/export_service.py` already has all three export methods (`export_raw`, `export_csv`, `export_json`) and the CLI already delegates to it correctly.
**Solution:** No service extraction needed for export -- only new API router endpoints wrapping the existing service. This is unlike import, analysis batch, waveform compare, and db vacuum which all need service-layer work.
**Impact:** Export is the simplest new API work in Phase 3.

## Verification

- **Phase 1:** `just check` passes (mypy + ruff + pytest), CLI commands produce identical output before/after refactoring
- **Phase 2:** `just check` passes, `just ui-generate-types` succeeds, new endpoints return correct responses (verify via Swagger UI at `/docs`)
- **Phase 3-4:** `npm run build && npm run type-check` pass, PrimeVue fully removed from `package.json`, all views render correctly, Playwright screenshot tests updated
- **Phase 5-7:** Each new view renders, fetches data, and handles error/empty states. End-to-end: import data via web UI, verify it appears in sessions/dashboard/stats

## Dependency Graph

```
Phase 1 (Services + CLI refactor) ──► Phase 2 (API endpoints)
                                          │
Phase 3 (Tailwind setup) ──► Phase 4 (PrimeVue swap)
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                    Phase 5 (Import, Export,    Phase 6 (Analysis mgmt,
                     Dashboard, Session detail)  DB, Validation, Day detail)
                              │                       │
                              └───────────┬───────────┘
                                          ▼
                                   Phase 7 (Polish)
```

Phases 1-2 (backend) complete first, then Phases 3-4 (design system), then Phases 5-7 (new views + polish). No context-switching between Python and frontend work.

## Status

- Phase 1: [9/9] Complete ✓
- Phase 2: [10/10] Complete ✓
- Phase 3: [9/9] Complete ✓
- Phase 4: [13/13] Complete ✓
- Phase 5: [4/4] Complete ✓ — PR #88 open (review fixes applied)
- Phase 6: [6/6] Complete ✓ — PR #89 open, stacked on #88 (review fixes applied)
- Phase 7: [6/6] Complete ✓ — PRs #90, #91, #92, #93 open

**Overall:** [57/57] Tasks Complete (100%) — All PRs open for review (#90 charts+sorting, #91 skeletons, #92 responsive sidebar, #93 OpenAPI audit).

## Plan Updates

### 2026-06-28 (session 6 — Phases 5+6 implementation)
- Phases 5 and 6 completed in full. PR #88 (Phase 5) and PR #89 (Phase 6, stacked on #88) opened.
- PR #88: https://github.com/wpfleger96/SNORE/pull/88 — Import, Export, Dashboard+, SessionDetail+, shared infra
- PR #89: https://github.com/wpfleger96/SNORE/pull/89 — Analysis Mgmt, Database, Validation, DayDetail, waveform compare, bulk delete
- Key implementation decisions:
  - Stacked PRs: Phase 5 branch from `origin/main`, Phase 6 branch from Phase 5 branch. After Phase 5 merges, retarget Phase 6 PR to main.
  - Sidebar restructuring pulled into Phase 5 (not deferred to Phase 7) — 10 links on a flat sidebar was unworkable.
  - Phase 6 stub views (`AnalysisManagementView`, `DatabaseView`, `ValidationView`, `DayDetailView`) created in Phase 5 commit to satisfy router import type-check.
  - ToggleGroup `@update:model-value` callback typed as `AcceptableValue` from reka-ui (union type including `number`, `bigint`, `Record`, `AcceptableValue[]`). Must cast: `if (v) ref = v as string`. Missing cast = TS error 2322.
  - `DayDetail.session_ids` is optional in the schema — guard with `?.length` in template.
  - `StatCard` requires `label` prop (not `title`) and `:value` as `number | null | undefined` (not string). Percentage values must be pre-multiplied: `value * 100` not formatted string.
  - Export blob endpoints cannot use `apiGet` wrapper (it returns `response.data` as JSON). Must use raw `api.get` with `{ responseType: 'blob' }` and destructure `{ data }` directly.
  - Parallel subagent workflow for Phase 5: Agent A (shared infra) ran first solo, then Agents B/C/D ran in parallel. For Phase 6: all 6 agents (E-J) ran in parallel (no inter-agent dependencies). Agent A wrote all 6 routes and all type re-exports to avoid router conflicts between parallel agents.
- Branches: `worktree-wpfleger-ui-phase5-high-impact` (commits `07be239`), `worktree-wpfleger-ui-phase6-remaining-views` (commit `cc74dc1`)
- Next: merge PR #88, retarget #89 to main, then merge #89. Remaining Phase 7 polish: column sorting, dark mode uPlot theming, responsive layout, skeleton loading states, type audit.

### 2026-06-28 (session 7 — Code review + fixes)
- 6-agent orchestrated code review (3 specialists × 2 PRs: Security & Reliability, Design & Simplicity, Functionality & Testing)
- 31 findings total: 3 🔴 MUST FIX, 20 🟡 SHOULD FIX, 8 🟢 CONSIDER — all addressed
- PR #88 review fixes (`66d97eb` → `03001fd`):
  - Removed dead-end import wizard Step 2 (options `forceReimport`/`sortOrder` were never sent to API; backend doesn't support them). Wizard collapsed from 4 steps to 3 (Source → Import → Results).
  - Fixed filesystem detect flow dead-end: `canProceed` now requires `selectedFiles !== null` (detect section is informational only).
  - Dashboard switched to `Promise.allSettled` for partial failure resilience.
  - Fixed NaN from unvalidated `?t=` query param, blob URL revocation timing (Safari), null event percentage rendering.
  - Extracted shared `.date-input` and `.filter-bar` CSS classes to `layout.css`.
  - Replaced hardcoded hex colors (`#92400e`, `#16a34a`) with CSS custom properties.
  - Added drag-and-drop directory structure warning, `accept` attribute on file input, date range validation on export, `toRef` swap, "Ventilation" section rename.
- PR #89 review fixes (`cc74dc1` → `d6fca4c`):
  - Added error handling to `handleVacuum` (was silently swallowing errors — `try...finally` with no `catch`).
  - Fixed batch dialog hiding errors behind modal overlay (close dialog on error before setting `error.value`).
  - Added null guards throughout AnalysisView (`machine_events?.length`, `mode_results ?? {}`, array `?? []`).
  - Fixed `pct()` null handling in ValidationView, added empty-state row.
  - Extracted `PaginationBar.vue` component from duplicated pagination blocks.
  - Standardized error display (`.error-state`), filter bar naming (`.filter-panel` → `.filter-bar`), removed `.mb-6` Tailwind shadow in DayDetailView.
  - Snapshot bulk delete IDs for race safety, added date validation, return type annotations.
- New shared infrastructure: `PaginationBar.vue` component, `.date-input` class, `.filter-bar` class in `layout.css`.
- CI: PR #88 all green (backend, e2e, ui). PR #89 pending full CI until retargeted to main after #88 merge.

### 2026-06-28 (session 8 — Phase 7 polish, all 5 remaining tasks)
- PRs #88 and #89 merged to main. Phase 7 implemented in 4 parallel-subagent worktrees, 4 new PRs opened.
- PR #90: https://github.com/wpfleger96/SNORE/pull/90 — Chart dark mode + column sorting (1 commit, 2 parallel agents)
- PR #91: https://github.com/wpfleger96/SNORE/pull/91 — Skeleton loading states (1 commit, 1 agent)
- PR #92: https://github.com/wpfleger96/SNORE/pull/92 — Responsive sidebar (1 commit, 1 agent)
- PR #93: https://github.com/wpfleger96/SNORE/pull/93 — OpenAPI type audit (1 commit, 2 sequential agents)
- Key implementation decisions:
  - **Sorting:** Manual `sortBy` ref over TanStack Table — backend already handles server-side sorting; `@tanstack/vue-table` adds abstraction without reducing code. Only `date-asc`/`date-desc`/`duration` backed on server; `session-id` sort available but not wired (no column for it).
  - **uPlot dark mode:** destroy+recreate is the only path (`chart.setOptions()` doesn't exist). `watch(isDark, () => createChart())` triggers full rebuild. `WaveformChart` conditions series colors too; `TrendChart` leaves series to parent props.
  - **Sheet close on navigate:** `watch(() => route.path, ...)` in `App.vue` (not `watch(route, ...)` which fires on query param changes). Simpler than threading an `onClose` callback prop through `AppSidebar`.
  - **shadcn-vue component installs:** Both `sheet` and `skeleton` install generated files with double-quote formatting. Always run `npx prettier --write src/components/ui/<name>/` immediately after install before committing.
  - **OpenAPI facade types:** `AnalysisFacade.run_analysis`/`get_analysis_result` returned `Any` despite `AnalysisService` being fully typed. Fix is to type the facade methods correctly (not `cast` in the router). `reras` field on `ModeResult` is optional in generated TypeScript (backend uses `default_factory=list`) — downstream code must guard with `?.length ?? 0`.
- New shared infrastructure: shadcn-vue `Sheet` component (`ui/src/components/ui/sheet/`), shadcn-vue `Skeleton` component (`ui/src/components/ui/skeleton/`).

### 2026-06-27 (session 5 — code review + fixes)
- 10-angle code review of PR #87 surfaced 15 findings (6 high, 5 medium, 4 low). All fixed in commit `80cab9c`.
- High-severity correctness bugs fixed:
  - `DeleteConfirmDialog`: `AlertDialogAction` wraps `DialogClose` — replaced with plain `Button` for async safety
  - `WaveformToolbar`: Toggle bound to `:pressed`/`@update:pressed` (slot props) instead of `:model-value`/`@update:model-value` — completely non-functional
  - `StatsView`/`AnalysisView`: `ToggleGroup type="single"` allows deselection → API error / vanishing table
  - `AnalysisView`: error state unreachable due to `v-else-if` ordering
  - `SessionDetailView`: events fetch failure collapsed entire session detail
- Medium-severity fixes: RxHistoryView even-row specificity, formatIso timezone bug, confirmDelete missing catch, getDevices unhandled rejection, useApiLoad race condition
- Low-severity fixes: useDarkMode FOUC + per-call watcher, EventExplorerView keyboard accessibility, tailwind.css dead HSL blocks, AlertDialogDescription for screen readers
- 7 new gotchas added to PLAN file documenting reka-ui pitfalls discovered during review
- Branch: `worktree-wpfleger-ui-design-system`, commit: `80cab9c`

### 2026-06-27 (session 4 — Phases 3+4 implementation)
- Phases 3 and 4 completed together in one session. PR #87 opened: https://github.com/wpfleger96/SNORE/pull/87
- Key implementation decisions:
  - `@lucide/vue` used instead of `lucide-vue-next` (deprecated)
  - Native `<input type="date">` used for date picker instead of shadcn Calendar+Popover (avoids `@internationalized/date` dependency)
  - No TanStack Table wrapper built — each DataTable migrated inline; use cases too varied for a generic wrapper
  - shadcn-vue init requires `@import 'tailwindcss'` in CSS + `paths` in root `tsconfig.json` to run successfully
  - `tailwind.css` uses full `@import 'tailwindcss'` (preflight included) — PrimeVue removed immediately rather than coexisting
  - `ui/src/lib/` is in root `.gitignore` (`lib/`) — must `git add -f`
  - `tailwind.css` needs `npx prettier --write` after init to pass CI format-check
  - ESLint override added for `vue/multi-word-component-names` in `src/components/ui/**/*.vue`
- Branch: `worktree-wpfleger-ui-design-system`
- Next session should begin Phase 5 (new views: Import, Export, Dashboard enhancement, Session Detail enhancement)

### 2026-06-27 (session 2 — implementation)
- Phases 1 and 2 implemented in full. PR #86 opened: https://github.com/wpfleger96/SNORE/pull/86
- Key implementation notes discovered during session:
  - `ImportService` constructor takes `db_session: Session` (for `service_dep()` compatibility), not `backup_root` — backup root passed as per-call kwarg
  - `BatchValidator` cannot use `service_dep()` because it takes `(db_session, profile=None)` — validation router injects `get_db` directly
  - `GET /sessions/{id}/waveforms/compare` must be defined before `GET /sessions/{id}/waveforms/{waveform_type}` to prevent FastAPI path param collision
  - `python-multipart` added as dependency for multipart file upload support
  - `ValidationReportResponse` schema was not needed — `ValidationReport` from `snore.validation` is already a proper Pydantic `BaseModel`
  - 8 new schemas added instead of the planned 6 (split `ImportResult` into `ImportSourceResult` + `ImportResult`, added `BatchSessionResult` alongside `BatchAnalysisResult`)
  - The `/sessions/{id}/analysis` endpoints initially left without `response_model` — resolved in session 8 by adding `response_model=AnalysisResult` after confirming the schema is stable at the top level (per-mode variability is in `ModeResult.metadata: dict[str, Any]` → `Record<string, unknown>`)
- Next session should start by running `just ui-generate-types` (after PR merges), then begin Phase 3 (Tailwind + shadcn-vue setup)

### 2026-06-27 (session 1 — planning)
- Restructured phase ordering: backend first (1-2), then design system (3-4), then new views (5-7). Avoids context-switching between Python and frontend; ensures all APIs are ready before any UI work begins.
- Initial plan created from research session covering CLI command audit (30 commands, ~85 options), service/API gap analysis, UI feature coverage map, and Vue 3 design system research (shadcn-vue, PrimeVue Volt, Nuxt UI v4)
