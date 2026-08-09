
# Plan: SNORE MCP Server — implementation steps (v3.8)

Supersedes v3.7: v3.8 folds the five Thufir pass-4 mechanical completions (5 IMPORTANT, event cd83618b) per Will's option-1 ruling (2026-08-02T22:22Z, event d94b59fe) — implementation released with NO further plan-review pass; the five items are named test obligations enforced in the PR code-review loops: universal terminal-payload durability rule (cancellation after import commit carries `import_committed` + `import_result`); `primary_mode` threaded through every invocation path (facade batch, coordinator, API request models, CLI); epoch contribution by data (`analyzed_session_count > 0`, nullable epoch `algorithm_identity` + `NO_DATA_IN_RANGE`); `RawWaveformChannel.sample_count`; nested `engine_versions_json` `{identity, run}` shape bound at step 4 + §14 (legacy flat rows read as stale). Supersedes v3.6: v3.7 folds the nine Thufir pass-3 corrections (6 IMPORTANT + 3 MINOR, event a2c792c6) per Will's option-2 ruling (2026-08-02T21:50Z, event ab504a3f): durable import-phase outcome on `ImportJob`; `leak_valid` + `recovery_detector` join algorithm identity; `AlgorithmIdentity`/`AnalysisRunMetadata` type split with explicit `primary_mode` at analysis invocation + `PRIMARY_MODE_MISMATCH` + total coverage-status precedence; waveform seam split into `fetch_waveform_window_raw` / `compute_waveform_window`; nullable `WindowResult.analysis_result_id` with per-window status; complete leak no-overlap branch (nearest-neighbor ≤5 s); typed/bounded channel lists + per-tool window caps; `get_breath_table` docstring ordering fix; `src/snore/parsers/register_all.py` added to PR-A's boundary. Supersedes v3.5: v3.6 folds Hayt's Appendix-A delta-2 (pass-2 F4'–F7' + MINORs 1–3) with Paul's editorial fixes. v3.5 superseded v3.4 after Thufir plan-review pass 2 (7 IMPORTANT + 4 MINOR findings accepted, thread 2b407054); v3.4 superseded v3.3 after pass 1 and Will's OAuth requirement (2026-08-02T20:16Z). Repo: `~/Development/SNORE` (main @ `1c2ed94`). v3.1 doctrine (tiered access, G1–G4) remains binding except where amended.

## Amended rulings

- **A1 (owner) + A5 (pass-1 F1 resolution):** Full programmatic analysis runs at import time. Lifecycle contract — **import is a committed phase; analysis is a post-commit second phase of the same command/job.** Import data is fully committed and ingested before analysis begins; analysis never holds or delays the import transaction. The CLI command and API job wait for both phases (with phase-distinct progress) rather than detaching an unowned background task — a detached `create_task()` has no owner across CLI loop shutdown / API worker lifetime. `--no-analyze` opts out. On-demand `snore analysis run` stays. *This is an interpretation of Will's "background job" wording: the data-ingestion guarantee is preserved; the process waits for the analysis phase with visible progress. Will may veto in favor of a durable detached job queue (more machinery).*
- **A2:** `get_nightly_summary` analysis-derived fields from latest `AnalysisResult`; absent → `null` + reason `analysis_not_run`.
- **A3:** Commit trailers: `Co-authored-by` + `Signed-off-by` for `Will Pfleger <pfleger.will@gmail.com>`.
- **A4:** `matplotlib` as main dependency (PR-B) + mypy override for `matplotlib.*`.
- **A6 (pass-1 F4 resolution — amends v3.1 "ISO 8601 with explicit offset"):** The DB deliberately stores device times as offset-free wall clock (models.py:16-26) — fabricating offsets would invent facts (violates G2) and make output host-timezone-dependent. Three-tier timestamp contract for all MCP output: (1) absolute audit instants (`UTCDateTime` columns) → ISO 8601 UTC with `Z`; (2) device/session wall-clock → offset-free ISO 8601 + explicit `timezone_status: "unknown"`; (3) positions within sessions (breaths, events, waveform windows) → numeric `offset_seconds` from session start. Non-UTC-host test (`TZ` env) proves identical source data yields identical MCP output.
- **A7 (owner, 2026-08-02T20:16Z): MCP OAuth is a hard requirement**, targeted at Claude iOS remote-MCP usage (enter server URL → browser OAuth consent → client stores tokens). Sequenced as **PR-C** (below), pending Will's confirmation.

## Approach

**Two PRs now + one auth follow-on**, parallel with disjoint boundaries, merged sequentially:

- **PR-A — the substrate** (Hayt): import-time post-commit analysis, `Breath` model, breath persistence, **and every reusable query/compute seam PR-B consumes** — not only `breath_service`. PR-B must stay a thin presentation layer (pass-1 F5): contextual events, nightly analysis aggregation + compliance, multi-channel detached waveform/render inputs, CA analysis all live in `services/**` as typed-DTO seams. Zero MCP knowledge; independently valuable. Merges first.
- **PR-B — the entire MCP layer** (Duncan): skeleton + all tools + resources + profiles. Tools are pure adapters: validate request → open scope → call service seam → enforce response limits → map typed DTO to MCP schema. Nothing else.
- **PR-C — MCP OAuth for hosted/Claude-iOS use** (owner TBD): follow-on after PR-B and after SNORE-multiuser Phases 1–2 (ActorContext + auth core) land. See "Auth" section.

**Service contract appendix (pass-1 F6):** the typed interface for every PR-A seam is written into this plan (Appendix A) **before implementation starts** — drafted by Hayt as owner, co-signed by Duncan as consumer, folded in by Paul, re-reviewed by Thufir in pass 2. Stage-2 tool work does not begin until the appendix is in the plan.

## Verified integration points (for executors)

- `BatchAnalysisCoordinator.submit` (services/analysis_facade.py:496): read blobs on loop → NumPy in `asyncio.to_thread` → write on loop, semaphore-capped. Reuse; do not build a second executor.
- `AnalysisService.store_result` (analysis/service.py:602) is **append-only**; latest-run selection is by `created_at` (facade:76-102,405-430); explicit latest/all deletion API exists (facade:309-363). These public semantics are preserved (see Breath versioning).
- `RawSessionBlobs`/`AnalysisInputs` (analysis/service.py:64,88) carry flow + machine events + SpO₂ + pulse ONLY — no leak or pressure channel. PR-A extends them (optional fields) for quality-flag derivation.
- CLI import: `cli/commands/import_data.py` loops sources → `asyncio.run(...)` per source. API import: `_run_import` worker thread + `api/import_jobs.py` registry with `ObserverChannel`.
- `_import_single_session` (database/importers.py:119-223) returns `(was_imported, day_id)`; extend to a typed outcome carrying `new_session.id` post-`flush()`; accumulate IDs only after each `begin_nested()` savepoint exits successfully; return per-chunk committed ID batches (importers.py:354-370, import_service.py:247-289). Force re-import returns the NEW session ID. No post-import re-query.
- `WaveformService.get_waveform_data` (waveform_service.py:76-133) closes its injected session after ONE channel — multi-channel windows need a new detached-DTO seam in PR-A.
- Fresh DBs: `Base.metadata.create_all` + alembic stamp (database/session.py:106-124); model-only `breaths` table appears on fresh DBs only; pre-existing DBs get capability-honest errors → drop + reimport.
- `session_scope()` uses module-global engine state (database/session.py:66-83) — there is no lifespan-injected factory today. See DB-access pattern below.
- Parser registration is explicit (`parsers/register_all.py:14-49`), not import-time automatic; parser metadata describes supported formats, not imported channels.
- Reference repos local: `~/Development/pagerduty-mcp-server` (`tool_error_boundary` server.py:42, `RESPONSE_SIZE_LIMIT` utils.py:15), `~/Development/JamBot` (`docs://schemas/{type}`).
- fastmcp 3.4.5 (Python 3.13 ok) verified in repo venv: in-memory client, direct `fastmcp.utilities.types.Image` returns, stdio + streamable-HTTP transports, `fastmcp.server.auth` with `OAuthProxy`/`oidc_proxy`/`jwt_issuer` + `google` provider (PR-C substrate).
- mypy strict everywhere.

## PRs

### PR-A — substrate: post-commit import analysis + breath persistence + all service seams — owner: @Hayt
**Branch:** `will/import-time-analysis` · **Merges first.**
**Boundary:** `src/snore/database/models.py`, `src/snore/database/importers.py`, `src/snore/analysis/**`, `src/snore/services/**`, `src/snore/api/**`, `src/snore/cli/commands/import_data.py`, `src/snore/parsers/register_all.py` (the `ensure_registered_parsers()` seam, Appendix A §15 — pass-3 MINOR), `tests/**` (non-MCP). **Do NOT touch:** `src/snore/mcp/**`, `src/snore/cli/__init__.py`, `src/snore/cli/commands/mcp.py`, `pyproject.toml`, `uv.lock`, `docs/mcp-server-plan.md` (PR-B's).

Steps:
1. **`Breath` model** (model only — NO Alembic migration, ruling #4). Breaths are **immutable versioned children of an analysis run**: FK `analysis_result_id` (CASCADE), unique `(analysis_result_id, breath_number)`, index `(analysis_result_id, start_time)`; denormalized `session_id` FK permitted for query efficiency (uniqueness stays on the run). Fields: timing (start/end offsets, Ti, Te, Ttot, I:E, duty cycle), amplitude (peak flow, tidal volume, rolling RR), existing `flatness_index`, NEW `mid_insp_flattening`, flow class + confidence, `is_recovery_breath`, inferred trigger/cycle (`experimental` + confidence + device-applicability; non-ResMed → `confidence: null, reason: "unvalidated_device"`), quality flags `leak_valid`/`ramp_active`/`mask_off` (nullable + reason). **Re-analysis appends a new run + new children; prior runs and their breaths are NEVER deleted except via the existing explicit deletion API** (preserves latest/all-version semantics; cascade handles children). All latest-run queries select the newest `AnalysisResult` per session ordered by `(created_at DESC, id DESC)` — the `id` tie-breaker makes provenance deterministic when concurrent runs share timestamp precision (pass-2 MINOR); one test pins equal-`created_at` behavior.
2. **Atomic persistence:** parent `AnalysisResult` + breath children written in one transaction; children keyed after `flush()` assigns the parent ID. Test parent+child rollback on child-insert failure.
3. **Quality-flag data contract (pass-1 F3, executable per pass-2 F3'):** extend `RawSessionBlobs`/`AnalysisInputs` with an optional leak channel + session/device metadata for provenance. Per-flag executable derivations, each versioned + nullable with reason codes:
   - `leak_valid` — source: the unified `leak` waveform (`WaveformType.LEAK_RATE`, L/min), sole source, no fallback channel. Threshold: breath is leak-valid iff mean leak over the breath's `[start, end)` interval `< 24.0 L/min` (the large-leak convention already used by the plan's `time-above-24` summary field), shipped as versioned constant `LEAK_VALID_ALGO = "v1"` with `LEAK_VALID_THRESHOLD_LPM = 24.0`. Alignment (complete branch, pass-3 IMPORTANT-6): leak samples are selected by timestamp overlap with the breath's `[start, end)` interval on the shared session-offset timebase. **Overlap exists → value = mean of the overlapping samples. No overlapping sample → nearest-neighbor: take the single leak sample nearest the breath midpoint; distance `<= 5 s` (max alignment gap — more than double the coarsest expected leak sampling interval) → use that sample's value; distance `> 5 s` → flag `null` + `channel_unaligned`.** No interpolation in v1 — any future interpolation must be separately specified and versioned. Absent channel → `null` + `channel_absent`. Boundary tests pin distance of exactly `5 s` (nearest-neighbor used) and just above `5 s` (`channel_unaligned`).
   - `ramp_active` — **v1 ships `null` + `not_available` unconditionally.** No ramp-state waveform exists in the unified model (parsers/unified.py:45-59 — pressure channels only); settings only prove ramp is *enabled*. Any future pressure-trajectory heuristic must be separately specified and versioned before use — do not infer in v1.
   - `mask_off` — **only** from a canonical mask-state signal/event; none is imported for any current source → `null` + `not_available`; never inferred from leak.
   Correct the v3.1 claim: quality flags are NOT flow-channel-derivable. Tests: absent leak channel, leak-alignment-gap exceeded, differing sample rates, ramp-enabled-in-settings still yields `ramp_active=null`, vendor applicability.
4. Mid-insp flattening extractor + trigger/cycle heuristic module in `analysis/shared/`, versioned constants; `engine_versions_json` becomes the **nested** `{"identity": AlgorithmIdentity.model_dump(), "run": AnalysisRunMetadata.model_dump()}` shape (binding — pass-4 IMPORTANT-5; exact shape in Appendix A §14 note 5; legacy flat rows read as stale — fresh-DB/reimport already mandated, no conversion code), where identity carries `format_version`, `segmenter`, `fl_classifier`, `flattening`, `trigger_cycle`, **`leak_valid`, `recovery_detector`** — pass-3 IMPORTANT-2: every query-driving derived feature is stamped and compared, so bumping `LEAK_VALID_ALGO` or the recovery-marker algorithm marks old rows stale instead of leaving them silently `OK`. **Primary-mode selection is explicit at analysis invocation (pass-3 IMPORTANT-3, threaded end-to-end per pass-4 IMPORTANT-2):** a `primary_mode` parameter is added through **every** analysis invocation path, not only `compute_analysis`/`run_analysis`: `AnalysisFacade.run_analysis`, `AnalysisFacade.run_batch_analysis` (analysis_facade.py:432-488), `BatchAnalysisCoordinator.submit` (analysis_facade.py:521-530), the API request models `AnalysisRunRequest` + `BatchAnalysisRequest` (api/schemas.py:73-75, 96-103), and CLI `analysis run`. Semantics everywhere: defaults to `DEFAULT_MODE` when it is among the requested modes; when the requested modes exclude `DEFAULT_MODE` (the on-demand CLI/API accept caller-supplied mode sets today, routers/analysis.py:68,113), the caller MUST supply `primary_mode` explicitly — `ValueError` in service/CLI paths, `422` at the API boundary; supplied `primary_mode` must be a member of `modes`, validated at every entry point. Import-time default-mode analysis needs no flag. Recovery markers are persisted from the chosen primary mode's detector run only; `primary_mode` is stored as run metadata (Appendix A §1). Tests pin, for both single and batch invocation with modes excluding `aasm`: missing `primary_mode` rejected; explicit `primary_mode` succeeds.
5. **Private compute envelope (pass-2 F2'):** `compute_analysis` returns `AnalysisComputation(summary: AnalysisResult, breaths: list[ComputedBreath])` — a private envelope, NOT an extension of the public `AnalysisResult` DTO. `programmatic_result_json` stores only `summary` (unchanged shape/size; no breath duplication in JSON — `store_result()` at analysis/service.py:623 writes `model_dump()` wholesale, so breaths must never enter that DTO). The coordinator write phase persists `summary` as the parent row + breaths as children in one transaction. `run_analysis()` and the on-demand API keep returning the existing public `AnalysisResult` type.
6. **Import hook (A5):** typed savepoint-derived session-ID capture (see integration points); `run_batch_analysis` gains a `session_ids` filter. CLI: import phase commits + prints, then analysis phase with per-session timing + progress; `--no-analyze`. API lifecycle contract (pass-2 F1', durability per pass-3 IMPORTANT-1): same job, two phases; **the job stays `RUNNING` across both phases** — terminal states remain job-level only. Add a typed `JobPhase` enum (`IMPORT`, `ANALYSIS`) and a **non-terminal** `phase_complete` event (carrying the committed import result) delivered via `ObserverChannel` at the import→analysis transition; SSE observers stay connected through it. **Durability rule: `phase_complete` is a live milestone only, never the sole evidence of committed data** — `ObserverChannel` coalesces non-terminal messages and late observers receive only the terminal payload (import_jobs.py:97-108, 192-196), so phase outcomes are persisted on the `ImportJob` itself: after the import phase commits, the job retains `import_result` and `import_committed=True`, and the rule is **universal (pass-4 IMPORTANT-1): EVERY terminal payload produced after `import_committed=True` — final success, analysis-phase failure, AND cancellation — includes `import_committed` + the retained `import_result`** (an analysis failure OR a cancellation after a committed import must never hide the fact that data landed; `try_cancel()`/`_finish_cancelled()` currently synthesize a bare `{"message": "Cancelled"}` terminal, import_jobs.py:238-262, 292-310 — the cancellation terminal is rebuilt to carry the retained import outcome). `_finish()` is called **exactly once**: after the analysis phase completes, or immediately after import when `--no-analyze` applies. Cancellation honored in both phases. Tests pin: import-committed-before-analysis ordering, `phase_complete` is non-terminal (SSE observer survives it and receives analysis progress), **stalled observer (non-terminal coalescing) still sees `import_committed` in the terminal payload, late observer (attached post-terminal) sees `import_committed` + `import_result`, analysis failure after import commit reports both the error and the committed import result, cancel during the analysis phase → late observer still sees `import_committed` + `import_result` in the cancellation terminal**, cancellation in each phase, `--no-analyze` single-finish.
7. **Service seams for PR-B (pass-1 F5), all typed DTOs per Appendix A:**
   - `services/breath_service.py` — windowed breath fetch, criteria window search, epoch × distribution stats, per-session `analysis_status` + algo-version metadata.
   - Contextual events seam (extend `EventService`) — per-event pressure/leak at event, MV prior 120 s, minutes since session start.
   - Nightly analysis aggregation seam — latest-run RERA/FL fields + usage-compliance calc (`compliance_pct`, `days_compliant`, threshold default 4 h) in the service layer (v3.1 original ruling), consumed by `get_nightly_summary`.
   - Multi-channel detached waveform-window seam — render inputs + raw windows (fixes single-channel session-closing limitation).
   - CA-analysis service — per-CA MV slope, PS delivered, stability index; night-level periodic-breathing % + MV rolling variance.

**Acceptance:** `just check` + `just test` + `just web-check` green; fresh-DB import populates `breaths`; two consecutive re-analyses yield two runs with correct latest selection and intact history; explicit deletion cascades; import commits before analysis begins (observable ordering test); `--no-analyze`; per-session timing; pre-existing-DB missing-table error is actionable; every seam unit-tested against fixture data independent of MCP; non-UTC-host determinism test (A6).

### PR-B — complete MCP layer (thin adapters only) — owner: @Duncan
**Branch:** `will/mcp-skeleton` (Stage 1 at `45153ab`; rework applied at `30c43e6`, unpushed pending gate) · **Merges second.**
**Boundary:** `src/snore/mcp/**`, `src/snore/cli/commands/mcp.py`, `src/snore/cli/__init__.py` (register only), `pyproject.toml`, **`uv.lock`**, `docs/mcp-server-plan.md`, MCP test files. **Do NOT touch:** `src/snore/database/**`, `src/snore/analysis/**`, `src/snore/services/**`, `src/snore/api/**` (PR-A's).

**Doctrine (pass-1 F5):** every tool = validate → open scope → call PR-A seam → size-guard → map DTO to MCP schema. No domain computation in `mcp/**`.

Stage 1 rework (applied at `30c43e6` — 1218 tests passing, `just check` green):
1. **Timestamp contract (A6)** across `schemas.py` and all tools; add `timezone_status`; positions as `offset_seconds`; non-UTC-host test.
2. **DB-access honesty (pass-1 M2):** tools deliberately use the existing module-global `session_scope()` behind ONE small scope-provider seam in `mcp/` (so PR-C can swap in actor-scoped sessions without tool rewrites — G3); plan/docs no longer claim lifespan factory injection; lifespan teardown calls `cleanup_database()` in `finally`.
3. **`docs://capabilities` (pass-1 M4):** idempotent parser-registration call (Stage 1 used `register_all_parsers()`; swaps to PR-A's `ensure_registered_parsers()` seam — Appendix A §15 — at seam adoption); channels/settings derived from DB rows; parser metadata as supported-parser context only. Cold-process test (no prior import command).
4. Keep: skeleton, profiles (G1), `tool_error_boundary`, `RESPONSE_SIZE_LIMIT`, `docs://tools`, `docs://schemas/{type}`, CLI entry.

Stage 1 seam adoption (after PR-A merges; rebase): `get_events` context and `get_nightly_summary` aggregation/compliance move from MCP-layer computation to PR-A seam calls.

Stage 2 — tuning tools (after Appendix A + PR-A merge): `get_breath_table` (≤15 min raw, binned beyond), `find_windows` (criteria enum per appendix), `compare_epochs` (leak-valid only; refuses cross-algo-version comparison); missing/empty `breaths` → capability-honest error.

Stage 3 — vision + CA: `render_window` — matplotlib Agg PNG returned as **`fastmcp.utilities.types.Image(data=..., format="png")`** (pass-1 M3), never a path or bytes-in-JSON; test MIME/type + payload signature via in-memory client; window ≤15 min (`window_cap_seconds=900`, Appendix A §9). `get_waveform` (LTTB, ≤2 min even with LTTB — `window_cap_seconds=120`, §9 — ≤1000 pts/channel). `get_ca_analysis` (adapter over PR-A's CA seam).

**Acceptance:** gates green; in-memory client tests for every tool + resource (happy path, empty DB, absent-channel nulls, missing-breaths-table, oversize guard, cold-process capabilities, non-UTC host); PNG content-type + vision sanity check documented in PR; manual stdio smoke test of the full success-criteria conversation documented in PR.

### PR-C — MCP OAuth (Claude iOS remote use) — follow-on, owner TBD
**Requirement (Will, locked):** Claude iOS connects to a remote MCP URL → immediate browser OAuth consent → client stores tokens. This means streamable-HTTP transport + OAuth 2.0 authorization-code + PKCE + dynamic client registration on the server side.
**Dependency chain:** per-user scoping and identity live in SNORE-multiuser (`PLANS/SNORE_MULTIUSER_PLAN.md` rev 3: `User`/`ActorContext`/Google OAuth/invites — its own review loop, channel SNORE-multiuser). MCP HTTP auth = validated token → `ActorContext` at the request boundary — exactly the G3 seam PR-B's scope provider isolates. Building MCP OAuth before ActorContext exists would invent a parallel identity system.
**Substrate verified:** fastmcp 3.4.5 ships `fastmcp.server.auth` (`OAuthProxy`, `oidc_proxy`, `jwt_issuer`, redirect validation, `google` provider) — supports fronting an upstream IdP or SNORE's own auth. Design deferred to PR-C planning; PR-B's only obligations now: transport parameter plumbed, zero ambient per-user state, scope-provider seam.
**Sequencing (confirmed by Will, 2026-08-02T20:32Z):** PR-C planning starts after SNORE-multiuser Phases 1–2 (schema + ActorContext, backend auth core) merge and PR-B lands — not the full multiuser track (its frontend/demo phases are irrelevant to token→ActorContext). PR-A/PR-B are NOT blocked; stdio MCP needs no auth. Claude iOS is the consumer that waits. Deployment (public URL via cloudflared) follows the homelab pattern in the multiuser track.

## Cross-cutting rules

- Worktrees off main, `will/<slug>`; PRs against `wpfleger96/SNORE` main; conventional commits; trailers per A3.
- `just check` / `just test` / `just web-check` green before PR; mypy strict, no unjustified `type: ignore`.
- Thufir plan review: passes 1–3 (budget) + pass 4 (Will-authorized, option 2) complete; per Will's option-1 ruling (2026-08-02T22:22Z, event d94b59fe) implementation is released at v3.8 with NO further plan-review pass — the five pass-4 items are named test obligations in the PR code-review loops. Then both PRs (separate loops, 3-pass budgets).
- PR descriptions cross-reference (related, not stacked).

## Tradeoffs Considered

| Option | Pros | Cons | Recommended |
|--------|------|------|-------------|
| Post-commit analysis phase awaited by command/job (A5) | Owned lifecycle: progress, cancellation, error visibility; import-commit guarantee kept | CLI process waits for analysis (opt-out via `--no-analyze`) | ✓ |
| Detached background task / durable job queue | CLI returns instantly | No owner across CLI loop close / API worker lifetime; or a whole new queue subsystem | |
| Breaths as children of immutable analysis runs | Preserves existing multi-version + deletion semantics; provenance free; no delete+reinsert logic | Old runs' breath rows occupy space until explicitly deleted (~10k rows/night/run) | ✓ |
| Delete old run to cascade breaths on re-analysis | One live run per session | Destroys history; changes public latest/all-version API semantics | |
| 2 PRs, PR-A owns ALL seams | Thin-presentation doctrine holds; seams reusable by API/CLI; PR-B reviewable as one layer | Free-tool seam adoption waits for PR-A merge (mitigated: contract appendix lets Duncan code against types) | ✓ |
| PR-B computes its own context/aggregation | More parallel | Domain logic in presentation layer; unavailable to API/CLI; violates doctrine | |
| PR-C after multiuser ActorContext | One identity system; MCP auth = token→ActorContext mapping | Claude iOS use waits for multiuser Phases 1–2 | ✓ |
| MCP-local OAuth now | iOS sooner | Parallel identity system, thrown away when ActorContext lands | |

## Open Questions

- ~~PR-C sequencing~~ — confirmed by Will 2026-08-02T20:32Z (after multiuser Phases 1–2 + PR-B; PR-A/PR-B unblocked).
- **Will (veto option):** A5's "post-commit phase awaited by the command/job" interpretation of the background-analysis ruling — proceeding with the recommendation unless vetoed.
- Import-time analysis modes: default mode set; `--all-modes` stays on-demand (assumed).

## Success Criteria

Unchanged from v3.1 (stdio conversation: overview → worst-FL windows → breath table → PNG → epoch comparison, generic contracts, `clinical_profile: uars`), plus: fresh `snore import` commits data before analysis begins and reports phase-distinct progress; `get_nightly_summary` degrades to `null` + `analysis_not_run`; identical MCP output on UTC and non-UTC hosts; (PR-C, later) Claude iOS completes browser OAuth against the hosted server and reaches the same tools with user-scoped data.

## Appendix A — Service contract (typed seams)

*Drafted by Hayt (owner, 2026-08-02T20:35Z), amended per Paul's review (20:38Z → delta-1 20:43Z), folded by Paul with three editorial corrections (WaveformWindowRequest session_id, calendar-night compliance denominator, `Waveform.waveform_type` column name). **Co-signed by Duncan (consumer) 2026-08-02T20:49Z — no conflicts.** Delta-2 (Hayt, 21:25Z — pass-2 F4'–F7' + MINORs 1–3) folded by Paul with editorial fixes: `trigger_cycle_reason` field replaces delta-2's vague "quality-flag slot" pairing wording; `find_windows` validation moved off the model (the delta's `model_validator` was a no-op) into the service; public `list_parsers()` (not `_parsers`) in the `ensure_registered_parsers()` sketch; `day_status`/`session_coverage` propagated to `FindWindowsResult` and `CaAnalysisResult` with explicit MIXED_VERSION refusal rules. **v3.7 (Paul, per Will's option-2 ruling): the nine Thufir pass-3 corrections folded using Thufir's fix language — §1 `AlgorithmIdentity`/`AnalysisRunMetadata` split + `CROSS_VERSION_REFUSAL_KEYS` + total `DayAnalysisStatus` precedence; §6 nullable window provenance; §7 primary-mode guard; §9 fetch/compute split + `WaveformChannelName` + per-tool caps; §10 policy revisions; §13 docstring alignment.** **v3.8 (Paul, per Will's option-1 ruling, event d94b59fe): the five Thufir pass-4 mechanical completions (event cd83618b) folded — step 6 universal terminal-payload durability (incl. cancellation); step 4 `primary_mode` end-to-end threading + nested `engine_versions_json` shape; §7 epoch contribution by data + nullable epoch identity; §9 `RawWaveformChannel.sample_count`; §10/§13 zero-current-PARTIAL wording sweep; §14 note 5 nested-shape binding.** All types live in `src/snore/services/breath_service.py`. Pydantic v2. Grounded against main @ `1c2ed94`.*

### Timestamp contract (A6 — three tiers, used throughout)

- **Tier 1** absolute audit instants → UTC ISO 8601 with `Z` (e.g. `AnalysisResult.created_at`)
- **Tier 2** device wall-clock → naive ISO 8601 + `timezone_status: "unknown"` (`Session.start_time`, `Event.start_time`)
- **Tier 3** in-session positions → `offset_seconds: float` from the anchoring session's `start_time`

No tier may fabricate a UTC offset for device wall-clock columns. Every DTO carrying `offset_seconds` also carries its anchoring session's tier-2 wall-clock start (`session_start_wall_clock` + `timezone_status`) so offsets are client-interpretable.

### 1. Shared building blocks

```python
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TimezoneStatus(StrEnum):
    UNKNOWN = "unknown"  # device wall-clock, no source TZ
    UTC = "utc"  # audit instants only


class NullReason(StrEnum):
    """Reason codes for null fields — G2 capability-honest contract."""

    ANALYSIS_NOT_RUN = "analysis_not_run"
    CHANNEL_ABSENT = "channel_absent"  # waveform row not in DB
    CHANNEL_UNALIGNED = "channel_unaligned"  # timestamps don't align
    UNVALIDATED_DEVICE = "unvalidated_device"  # heuristic not tuned for vendor
    NO_DATA_IN_RANGE = "no_data_in_range"
    ALGO_VERSION_MISMATCH = "algo_version_mismatch"  # cross-version comparison refused
    PRIMARY_MODE_MISMATCH = (
        "primary_mode_mismatch"  # RERA/recovery aggregation refused:
    )
    # contributing runs differ in primary_mode
    TABLE_MISSING = "table_missing"  # breaths table absent (pre-existing DB)
    ANALYSIS_STALE = "analysis_stale"  # engine_versions changed since last run
    NOT_AVAILABLE = "not_available"  # data exists but can't be derived
    RX_CHANGED_WITHIN_EPOCH = "rx_changed_within_epoch"  # epoch RX-homogeneity guard


class AnalysisStatus(StrEnum):
    NOT_RUN = "not_run"  # no AnalysisResult row for this session
    STALE_VERSION = "stale"  # AnalysisResult exists but engine_versions_json differs
    # from current algo constants
    OK = "ok"  # latest run matches current versions


class DayAnalysisStatus(StrEnum):
    """
    Day-level coverage status for multi-session aggregation (pass-2 F4';
    TOTAL precedence rule per pass-3 IMPORTANT-3 — evaluate in order, first match wins,
    so every day maps to exactly one state):

    1. MIXED_VERSION — ≥2 OK sessions differ on any cross-version refusal key
       (CROSS_VERSION_REFUSAL_KEYS, §1) → FL aggregation refused for this day.
    2. OK            — all eligible sessions current (OK) with identical identity.
    3. NOT_RUN       — no session in this day has any AnalysisResult.
    4. STALE         — every session has results, all stale-version.
    5. PARTIAL       — CATCH-ALL heterogeneous coverage: any other mix (current+missing,
       current+stale, stale+not-run with no current session, ...).
    """

    OK = "ok"
    PARTIAL = "partial"
    MIXED_VERSION = "mixed_version"
    NOT_RUN = "not_run"
    STALE = "stale"


class AlgorithmIdentity(BaseModel):
    """
    Comparable algorithm identity (pass-3 IMPORTANT-2/-3: identity is its own type,
    separate from run metadata — not comments on a mixed model). Staleness detection
    (_current_algorithm_identity(), §13) compares ALL of these fields and ONLY these.
    Every query-driving derived feature is stamped: bumping any version marks old
    rows STALE_VERSION instead of leaving them silently OK.
    """

    format_version: int  # currently 2; bump to 3 when Breath rows land
    segmenter: str  # e.g. "v1.0"
    fl_classifier: str  # e.g. "v1.0"
    flattening: str  # covers both flatness_index and mid_insp_flattening
    trigger_cycle: str  # always present, e.g. "v1.0-experimental";
    # per-breath applicability lives in Breath rows
    leak_valid: str  # LEAK_VALID_ALGO — drives worst-window eligibility and
    # every leak-valid epoch distribution (pass-3 IMPORTANT-2)
    recovery_detector: str  # recovery-marker algorithm — drives persisted
    # is_recovery_breath rows (pass-3 IMPORTANT-2)


# ONE cross-version refusal definition, shared by the day-level MIXED_VERSION check
# and the epoch guard (§7) — pass-3 IMPORTANT-2 "same identity definition" requirement.
# All identity fields EXCEPT trigger_cycle: trigger/cycle is experimental annotation;
# its version differences are data provenance, not algo incompatibility (pass-2 ruling).
CROSS_VERSION_REFUSAL_KEYS: tuple[str, ...] = (
    "format_version",
    "segmenter",
    "fl_classifier",
    "flattening",
    "leak_valid",
    "recovery_detector",
)


class AnalysisRunMetadata(BaseModel):
    """Descriptive run metadata — NEVER part of staleness or cross-version refusal."""

    primary_mode: str  # explicit at analysis invocation (PR-A step 4): defaults
    # to DEFAULT_MODE ("aasm", analysis/modes/config.py:95)
    # when included in the requested modes; otherwise the
    # caller MUST supply it (ValueError). The mode whose
    # recovery markers were persisted (§10 mode policy).
    modes: list[str]  # all detection modes included in this run


class AlgoVersions(BaseModel):
    """
    Per-run contents of AnalysisResult.engine_versions_json (extended by PR-A).
    Composition of the two separate types above — staleness compares .identity
    ONLY, structurally rather than by convention (pass-3 IMPORTANT-3).
    """

    identity: AlgorithmIdentity
    run: AnalysisRunMetadata


class SessionCoverage(BaseModel):
    """
    Per-session analysis coverage entry (pass-2 F4'). Carries the FULL per-run
    AlgoVersions (identity + run metadata) so per-session provenance — including
    primary_mode — is preserved even when day-level aggregation refuses
    (pass-3 IMPORTANT-3).
    """

    session_id: int
    analysis_status: AnalysisStatus  # per-session: OK | STALE_VERSION | NOT_RUN
    algo_versions: AlgoVersions | None
```

### 2. `BreathQueryRange` — the universal range selector

```python
class BreathQueryRange(BaseModel):
    """
    Identifies a contiguous waveform window within a therapy day.

    Device disambiguation: if device_id is None and the day has sessions
    from exactly one device, that device is used; if multiple devices
    exist, device_id is required (ValueError lists available IDs).

    session_id semantics:
    - None + exactly one session that day → that session is used.
    - None + multiple sessions → MultiSessionAmbiguityError. Never silently picks one.
    - Non-None → offsets relative to that session's start_time; ValueError if the
      session_id doesn't belong to the requested therapy_date + device.
    """

    therapy_date: date
    device_id: int | None = None  # None = single-device auto-select
    session_id: int | None = None  # required when day has >1 session
    offset_start: float = Field(ge=0.0)
    offset_end: float = Field(gt=0.0)

    # Pagination / binning for breath fetch
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=500, ge=1, le=2000)
    bin_minutes: float | None = Field(default=None, ge=1.0)

    @model_validator(mode="after")
    def validate_window(self) -> "BreathQueryRange":
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be > offset_start")
        window_minutes = (self.offset_end - self.offset_start) / 60
        if self.bin_minutes is None and window_minutes > 15:
            raise ValueError(
                f"Raw window {window_minutes:.1f} min exceeds 15-min cap; "
                "set bin_minutes to aggregate"
            )
        return self


class SessionSummary(BaseModel):
    session_id: int
    start_wall_clock: datetime  # naive — tier-2 device wall-clock
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    duration_seconds: float


class MultiSessionAmbiguityError(Exception):
    """
    Raised when session_id is required but not supplied for a multi-session day.
    PR-B catches this and converts it to a structured MCP error response listing
    the sessions so the caller can re-issue with session_id set.
    """

    def __init__(
        self, therapy_date: date, device_id: int, sessions: list[SessionSummary]
    ) -> None:
        self.therapy_date = therapy_date
        self.device_id = device_id
        self.sessions = sessions
        super().__init__(
            f"Multiple sessions on {therapy_date}: pass session_id to disambiguate"
        )
```

### 3. `Breath` ORM model (column summary)

```python
# src/snore/database/models.py addition (PR-A step 1)
# Unique: (analysis_result_id, breath_number)
# Index:  (analysis_result_id, start_offset_seconds)
#
# analysis_result_id  FK → analysis_results.id  CASCADE DELETE
# session_id          FK → sessions.id          CASCADE DELETE  (denorm for fast join)
# breath_number       int    sequential within session
# start_offset_seconds float  seconds from Session.start_time
# end_offset_seconds   float
# ti / te / ttot      float  breath timing (s)
# ie_ratio            float
# duty_cycle          float  Ti/Ttot
# peak_insp_flow      float  L/min
# peak_exp_flow       float  L/min
# tidal_volume        float  mL
# flatness_index      float  time>80%-peak (existing ShapeFeatures field)
# mid_insp_flattening float  mid-insp flow / peak (NEW)
# flow_class          int    1–7
# flow_class_confidence float 0–1
# is_recovery_breath  bool   from the primary mode's detector run only (§10 mode policy)
# trigger_type        str | None  TriggerType values (experimental)
# cycle_type          str | None  CycleType values (experimental)
# trigger_cycle_confidence float | None
# trigger_cycle_applicability str | None  TriggerCycleApplicability values
# leak_valid          bool | None   + leak_valid_reason (NullReason)
# ramp_active         bool | None   + ramp_active_reason
# mask_off            bool | None   always None unless canonical mask signal exists
# mask_off_reason     str | None    NullReason.NOT_AVAILABLE when None
#
# NOTE: trigger_cycle_experimental is NOT a column — it is a serialized DTO constant
# (Literal[True], §4). trigger_cycle_reason is likewise DTO-derived from
# trigger_cycle_applicability, not stored.
```

### 4. `BreathRow` — DTO for a single persisted breath

```python
class TriggerType(StrEnum):
    NORMAL = "normal"
    PREMATURE = "premature"
    DELAYED = "delayed"


class CycleType(StrEnum):
    NORMAL = "normal"
    PREMATURE = "premature"


class TriggerCycleApplicability(StrEnum):
    VALIDATED = "validated"
    UNVALIDATED_DEVICE = "unvalidated_device"


class BreathRow(BaseModel):
    """One row from the breaths table — matches the ORM column set above."""

    analysis_result_id: int
    session_id: int
    breath_number: int

    # Anchor (tier-2) + positions (tier-3)
    session_start_wall_clock: datetime  # naive — tier-2
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    start_offset_seconds: float
    end_offset_seconds: float

    # Timing
    ti: float
    te: float
    ttot: float
    ie_ratio: float
    duty_cycle: float

    # Amplitude
    peak_insp_flow: float  # L/min
    peak_exp_flow: float  # L/min
    tidal_volume: float  # mL

    # Flow limitation features (both versioned via AlgoVersions)
    flatness_index: float  # time>80%-peak (0–1)
    mid_insp_flattening: float  # mid-insp flow / peak (0–1)

    # Classification
    flow_class: int  # 1–7
    flow_class_confidence: float  # 0–1
    is_recovery_breath: bool  # from the primary mode's detector run only (§10)

    # Trigger/cycle heuristic (experimental)
    trigger_type: TriggerType | None
    cycle_type: CycleType | None
    trigger_cycle_confidence: float | None
    trigger_cycle_experimental: Literal[True] = True
    # Serialized Pydantic field, NOT ClassVar — ClassVar attributes are excluded
    # from model_dump() and model_json_schema() (verified empirically, pass-2 F7'),
    # which would silently strip the mandatory experimental marker from every
    # MCP response and docs://schemas. Not a DB column (§3 note stands).
    trigger_cycle_applicability: TriggerCycleApplicability | None
    trigger_cycle_reason: NullReason | None
    # Pairing rule (F7'): applicability=UNVALIDATED_DEVICE ⇒ trigger_type,
    # cycle_type, trigger_cycle_confidence all None and
    # trigger_cycle_reason=NullReason.UNVALIDATED_DEVICE.
    # applicability=VALIDATED ⇒ trigger_cycle_reason=None.

    # Quality flags
    leak_valid: bool | None
    leak_valid_reason: NullReason | None
    ramp_active: bool | None
    ramp_active_reason: NullReason | None
    mask_off: bool | None  # always None until canonical signal exists
    mask_off_reason: NullReason | None  # NullReason.NOT_AVAILABLE
```

### 5. `BreathPage` — paginated or binned fetch result

```python
class BreathBin(BaseModel):
    """Aggregated metrics for one time bin (a bin is always within one session)."""

    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    bin_start_offset: float  # seconds from session start
    bin_end_offset: float
    breath_count: int
    flatness_index_median: float | None
    mid_insp_flattening_median: float | None
    flow_class_mode: int | None  # most common class in bin
    tidal_volume_median: float | None  # mL
    ie_ratio_median: float | None
    leak_valid_fraction: float | None  # fraction of breaths with leak_valid=True
    analysis_status: AnalysisStatus


class BreathPage(BaseModel):
    """Result of get_breath_table()."""

    query: BreathQueryRange
    analysis_status: AnalysisStatus
    algo_versions: AlgoVersions | None  # None when status != OK
    null_reason: NullReason | None  # set when status != OK
    is_binned: bool
    total_breaths: int  # matching the window (pre-pagination)
    page: int
    page_size: int
    # Exactly one of rows/bins is populated:
    rows: list[BreathRow] = Field(default_factory=list)  # raw rows (is_binned=False)
    bins: list[BreathBin] = Field(default_factory=list)  # aggregated (is_binned=True)
    # Ordering: ascending (session_id, breath_number) — deterministic.
    # Tie-breaking: breath_number unique within analysis_result_id; no ties possible.
```

### 6. `WindowCriterion` — find_windows criteria enum

```python
class WindowCriterion(StrEnum):
    WORST_FLATTENING_LEAK_VALID = "worst_flattening_leak_valid"
    # N windows ranked by worst (highest) mid_insp_flattening among breaths with
    # leak_valid=True. Excludes leak_valid IS NULL unless include_unknown_leak=True.

    CA_CENTERED = "ca_centered"
    # N windows centered on CA events (event_type='CA'), ±context_seconds around CA start.

    FL_RUN_ENDING_IN_RECOVERY = "fl_run_ending_in_recovery"
    # RERA-proxy: runs of ≥min_fl_run_length consecutive flow-limited breaths
    # (flow_class >= fl_class_threshold) ending with is_recovery_breath=True.
    # Window spans first FL breath → recovery breath end.


class WindowCriterionOptions(BaseModel):
    """
    Criterion-specific options (defaults documented per field).

    Criterion-irrelevant option validation happens in BreathService.find_windows()
    — NOT on this model — because the bound criterion is a find_windows() argument,
    not a field here. The service raises ValueError naming the offending fields when
    any option irrelevant to the bound criterion differs from its default (rejected,
    not ignored).
    """

    # WORST_FLATTENING_LEAK_VALID:
    include_unknown_leak: bool = False
    flattening_threshold: float | None = None  # None = top-N regardless of value
    min_window_breaths: int = 3  # minimum breaths to form a reportable window
    context_breaths_before: int = Field(default=3, ge=0)  # breaths prepended to anchor
    context_breaths_after: int = Field(default=3, ge=0)  # breaths appended to anchor
    # CA_CENTERED:
    context_seconds: float = 120.0
    # FL_RUN_ENDING_IN_RECOVERY:
    min_fl_run_length: int = 2
    fl_class_threshold: int = 4


class WindowResult(BaseModel):
    """One found window."""

    criterion: WindowCriterion
    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    window_start_offset: float
    window_end_offset: float
    reason_summary: str  # e.g. "fl_index=0.71, 5 FL breaths"
    worst_mid_insp_flattening: float | None  # WORST_FLATTENING criterion
    fl_run_length: int | None  # FL_RUN_ENDING_IN_RECOVERY
    anchor_event_offset: float | None  # CA_CENTERED: CA start offset
    # Per-window analysis provenance (pass-3 IMPORTANT-5): CA_CENTERED windows are
    # anchored on machine Event rows and remain valid on sessions with NO analysis
    # run (capability-honest: the machine-event window is returned with null
    # analysis provenance; waveform rendering needs no analysis row).
    analysis_result_id: int | None  # which analysis run produced these breaths;
    # None when the window's session has no run
    analysis_status: AnalysisStatus  # the window's session status:
    # NOT_RUN ⇒ analysis_result_id=None
    analysis_reason: NullReason | None  # ANALYSIS_NOT_RUN when analysis_result_id=None


class FindWindowsResult(BaseModel):
    """Result of find_windows()."""

    query_date: date
    device_id: int
    criterion: WindowCriterion
    day_status: DayAnalysisStatus  # coverage policy, §10 (pass-2 F4')
    session_coverage: list[SessionCoverage] = Field(default_factory=list)
    algorithm_identity: (
        AlgorithmIdentity | None
    )  # uniform identity of contributing runs;
    # only identity is guaranteed uniform at day
    # level — full per-run AlgoVersions (incl.
    # run metadata) lives in session_coverage
    # (pass-3 IMPORTANT-3 type separation)
    null_reason: NullReason | None
    primary_mode: str | None  # mode whose recovery markers were used
    # (FL_RUN_ENDING_IN_RECOVERY); None when the
    # criterion does not use recovery markers
    windows: list[WindowResult]  # ordered by severity (worst first)
    # MIXED_VERSION rule: FL-ranked criteria (WORST_FLATTENING_LEAK_VALID,
    # FL_RUN_ENDING_IN_RECOVERY) refuse on day_status=MIXED_VERSION — ranking
    # breaths across CROSS_VERSION_REFUSAL_KEYS differences is a cross-version
    # comparison → windows=[], null_reason=ALGO_VERSION_MISMATCH.
    # Primary-mode rule (pass-3 IMPORTANT-3): FL_RUN_ENDING_IN_RECOVERY additionally
    # requires a uniform primary_mode across the day's contributing runs — recovery
    # markers from different primary modes are not comparable; mixed →
    # windows=[], null_reason=PRIMARY_MODE_MISMATCH (per-run metadata preserved
    # in session_coverage). CA_CENTERED proceeds on ANY day_status including
    # NOT_RUN (event-anchored; per-window analysis provenance is nullable, §6).
    # Dedup: windows overlapping >50% of the shorter window are merged; the
    # highest-severity window in the merged group is kept.
    # Ordering: descending by worst_mid_insp_flattening or fl_run_length,
    # ascending window_start_offset for ties.
```

**Window construction rule — `WORST_FLATTENING_LEAK_VALID` (pass-2 F5', binding):**

1. Collect eligible anchor breaths: `leak_valid=True` (`include_unknown_leak=True` extends eligibility to `leak_valid IS NULL`). Apply `flattening_threshold` if set.
2. Rank anchors descending by `mid_insp_flattening`.
3. For each anchor (highest first), form a candidate window:
   a. Take the `context_breaths_before` breaths immediately before the anchor and `context_breaths_after` immediately after, all from the same session.
   b. Clamp to session bounds: fewer preceding breaths than requested → start at the session's first breath; same at the end.
   c. Window bounds: `start_offset` = first breath's `start_offset_seconds`, `end_offset` = last breath's `end_offset_seconds`.
   d. The anchor is always included; discard the candidate if total breaths in the window < `min_window_breaths`.
4. Deduplicate: candidates overlapping >50% of the shorter window merge; keep the member with the highest anchor `mid_insp_flattening`.
5. Return top-N after dedup, ordered descending `mid_insp_flattening`, ascending `window_start_offset` for ties.

Criterion-irrelevant options (here: `context_seconds`, `min_fl_run_length`, `fl_class_threshold`) that differ from defaults → `ValueError` naming the fields (see `WindowCriterionOptions` docstring). The same rejection rule applies symmetrically to the other two criteria.

### 7. Epoch DTOs — compare_epochs

```python
class EpochRequest(BaseModel):
    """One settings epoch for comparison."""

    label: str  # e.g. "before" / "after"
    date_start: date
    date_end: date
    device_id: int | None = None  # None = single-device auto-select
    # Algo-version guard: all nights in this epoch must share the same
    # AlgorithmIdentity values for every CROSS_VERSION_REFUSAL_KEYS field (§1 —
    # the ONE shared refusal definition). Any night differing → ValueError with
    # offending dates; cross-epoch mismatch → NullReason.ALGO_VERSION_MISMATCH.
    # Primary-mode guard (pass-3 IMPORTANT-3): RERA-derived fields (rera_proxy_count)
    # require a uniform primary_mode across all contributing runs in the epoch;
    # mixed → rera_proxy_count=None + rera_reason=PRIMARY_MODE_MISMATCH (FL
    # distributions still returned — FL needs identity uniformity only, not mode).
    # RX-homogeneity guard: before computing distributions, validate all RX_KEYS
    # values uniform across every night in this epoch (via RxTracker period query).
    # If any key changed mid-epoch → refuse with null_reason=RX_CHANGED_WITHIN_EPOCH
    # and populate rx_violations (label, changed keys, change dates); the caller
    # splits the range at the change boundary and re-issues.


class DistributionMetric(StrEnum):
    MID_INSP_FLATTENING = "mid_insp_flattening"
    FLATNESS_INDEX = "flatness_index"
    TIDAL_VOLUME_ML = "tidal_volume_ml"
    IE_RATIO = "ie_ratio"


class DistributionStats(BaseModel):
    """Descriptive stats for one metric over one epoch (leak-valid breaths only)."""

    median: float | None
    iqr: float | None  # p75 - p25
    p95: float | None
    n_breaths: int  # leak-valid breaths counted
    n_nights: int  # nights with ≥1 leak-valid breath


class EpochRxViolation(BaseModel):
    epoch_label: str
    changed_keys: list[str]  # which RX_KEYS changed within this epoch
    change_dates: list[date]  # dates where a change was detected


class EpochBreathStats(BaseModel):
    """Breath-feature distributions for one epoch."""

    label: str
    date_start: date
    date_end: date
    nights_with_data: int  # nights that CONTRIBUTE data: analyzed_session_count > 0
    # (contribution by data, not enum label — pass-4
    # IMPORTANT-3: a PARTIAL night with zero OK sessions
    # has no current breaths and does NOT contribute)
    nights_missing_analysis: int  # nights with analyzed_session_count == 0, whatever
    # their day_status (NOT_RUN, STALE, MIXED_VERSION,
    # and zero-current PARTIAL)
    algorithm_identity: (
        AlgorithmIdentity | None
    )  # uniform identity across all contributing
    # nights (CROSS_VERSION_REFUSAL_KEYS guard);
    # None + null_reason=NO_DATA_IN_RANGE when NO
    # nights contribute (typed empty-epoch result,
    # pass-4 IMPORTANT-3). Only identity is guaranteed
    # uniform — run metadata is per-run (nightly
    # session_coverage); a singular full AlgoVersions
    # here would mislabel mixed-mode epochs
    # (pass-3 IMPORTANT-3)
    null_reason: NullReason | None  # NO_DATA_IN_RANGE for an empty epoch
    primary_mode: str | None  # uniform mode whose recovery markers sourced
    # rera_proxy_count; None when contributing runs
    # mix primary modes (pass-3 IMPORTANT-3)
    mid_insp_flattening: DistributionStats
    flatness_index: DistributionStats
    flow_class_distribution: dict[
        int, int
    ]  # {flow_class: breath_count}, leak-valid only
    tidal_volume_ml: DistributionStats
    ie_ratio: DistributionStats
    rera_proxy_count: int | None  # FL-run-ending-in-recovery events in epoch;
    # None + rera_reason=PRIMARY_MODE_MISMATCH when
    # contributing runs mix primary modes (per-session
    # metadata preserved via nightly session_coverage)
    rera_reason: NullReason | None
    rx_settings: dict[str, str]  # RX_KEYS values (uniform per RX guard)
    # Coverage rule (pass-2 F4', contribution per pass-4 IMPORTANT-3): a night
    # contributes to epoch distributions ONLY when analyzed_session_count > 0;
    # days with day_status=MIXED_VERSION are excluded entirely — cross-version
    # breaths cannot safely mix. DistributionStats.n_nights counts only
    # contributing nights (analyzed_session_count > 0).


class CompareEpochsResult(BaseModel):
    """Result of compare_epochs()."""

    epochs: list[EpochBreathStats]  # same order as request
    null_reason: NullReason | None  # set if comparison refused
    rx_violations: list[EpochRxViolation] = Field(default_factory=list)
    # Populated when null_reason = RX_CHANGED_WITHIN_EPOCH; one entry per failing epoch.
    # Algo-version refusal (exact): refused when ANY CROSS_VERSION_REFUSAL_KEYS field
    # (§1) differs between epochs. NOT refused for trigger_cycle or run-metadata
    # (primary_mode / modes) differences — trigger_cycle is experimental annotation;
    # mode differences degrade only RERA fields (PRIMARY_MODE_MISMATCH per epoch, above).
```

### 8. Contextual-event DTO

```python
class ContextualEvent(BaseModel):
    """
    One machine-flagged event with surrounding context. Waveform context comes
    from the WaveformWindow seam (§9) — never direct WaveformService calls.
    """

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2; anchor for offset_seconds
    event_type: str  # from Event.event_type
    event_start_wall_clock: datetime  # naive — tier-2
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    offset_seconds: float  # from session start
    duration_seconds: float | None

    # Context (all nullable — channel may be absent)
    pressure_at_event_cmh2o: float | None
    pressure_reason: NullReason | None
    leak_at_event_lpm: float | None
    leak_reason: NullReason | None
    mv_prior_120s_lpm: float | None  # mean MV in 120 s before event
    mv_reason: NullReason | None
    minutes_since_session_start: float
```

### 9. Multi-channel waveform-window DTO (NEW seam — replaces direct WaveformService)

**Ownership contract (pass-3 IMPORTANT-4):** the seam is split into a typed fetch function and a pure compute function so scope ownership is structural, never a promise a method cannot keep — `BreathService` holds a caller-injected `AsyncSession` (§13) and MUST NOT close it, and no code reaches around the actor-aware scope provider (M2/PR-C) via an internal global `session_scope()`:

```python
async def fetch_waveform_window_raw(
    db: AsyncSession, request: WaveformWindowRequest
) -> RawWaveformWindow:
    """DB I/O ONLY. One query — SELECT ... FROM waveforms WHERE session_id = :sid
    AND waveform_type IN (:types) (UniqueConstraint(session_id, waveform_type)
    guarantees ≤1 row per channel, models.py:280) — copies bytes + scalar metadata
    into the detached RawWaveformWindow and returns. Never closes db: the scope
    owner (the MCP tool via the scope-provider seam, or any other caller) opens
    and closes the scope around this call. Single query = single snapshot across
    channels."""


def compute_waveform_window(raw: RawWaveformWindow) -> WaveformWindow:
    """Pure — no DB access. Deserializes bytes using each channel's persisted
    sample_count (pass-4 IMPORTANT-4), slices by offset window, applies LTTB
    when max_points is set. Runs after the DB scope has closed. A blob whose
    byte length and sample_count mismatch raises the existing sanitized
    invalid-waveform error (test pinned)."""
```

`RawWaveformWindow` is a small detached DTO — nothing in it holds a DB handle:

```python
class RawWaveformChannel(BaseModel):
    waveform_type: WaveformChannelName
    unit: str | None
    sample_rate: float
    sample_count: int  # persisted Waveform sample count, copied in the
    # batched query — the deserializer requires blob +
    # count (the existing fetch seam returns
    # (data_blob, sample_count, metadata) for exactly
    # this reason, waveform_loader.py:170-222);
    # pass-4 IMPORTANT-4
    raw_bytes: bytes  # serialized samples, copied out of the ORM row


class RawWaveformWindow(BaseModel):
    request: WaveformWindowRequest  # the validated request
    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    channels: list[RawWaveformChannel]
    missing_channels: list[WaveformChannelName]
```

`BreathService.get_waveform_window()` (§13) is retained as the convenience orchestrator: it awaits `fetch_waveform_window_raw(self._db, request)` using its injected session — **without closing it** — then returns `compute_waveform_window(raw)`; callers that want compute outside the scope (MCP render/raw tools SHOULD, so deserialization/LTTB never run inside an open transaction) call the two functions directly around their own scope boundary.

```python
class WaveformChannelName(StrEnum):
    """Typed channel names (pass-3 MINOR-1) — the persisted Waveform.waveform_type
    values (models.py:268), sourced from parsers/unified.py WaveformType values.
    Unknown/blank channel strings are rejected at request validation, NOT converted
    into missing_channels; missing_channels reports only VALID names absent from
    the DB for this session."""

    FLOW = "flow"
    PRESSURE = "pressure"
    THERAPY_PRESSURE = "therapy_pressure"
    EPAP = "epap"
    LEAK = "leak"
    MV = "mv"
    RR = "rr"
    TV = "tv"
    SPO2 = "spo2"
    PULSE = "pulse"
    FL = "fl"
    SNORE = "snore"


class WaveformChannel(BaseModel):
    """One deserialized, windowed waveform channel."""

    channel_type: (
        WaveformChannelName  # sourced from Waveform.waveform_type (models.py:268)
    )
    unit: str | None
    sample_rate: float
    offset_seconds: list[float]  # positions from session start
    values: list[float]
    original_sample_count: int  # pre-LTTB count
    is_downsampled: bool


class WaveformWindow(BaseModel):
    """
    Multi-channel waveform data for a time window. Produced by
    compute_waveform_window() from a detached RawWaveformWindow (fetched in ONE
    query under one short scope — see the ownership contract above). This seam
    replaces — never calls — the old single-channel
    WaveformService.get_waveform_data() path, which closes its session after one
    channel (waveform_service.py:115).
    """

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    window_start_offset: float
    window_end_offset: float
    channels: list[WaveformChannel]  # all requested channels that exist
    missing_channels: list[
        WaveformChannelName
    ]  # valid requested channels absent from DB
    missing_channel_reason: (
        NullReason | None
    )  # CHANNEL_ABSENT when ≥1 missing; None when none


class WaveformWindowRequest(BaseModel):
    """session_id semantics identical to BreathQueryRange: None + multiple sessions
    on the day → MultiSessionAmbiguityError; non-None → offsets relative to that
    session's start_time, ValueError if it doesn't belong to the date + device.

    Per-tool window caps (pass-3 MINOR-1): the CALLER sets window_cap_seconds —
    get_waveform passes 120 (raw-array escape hatch stays ≤2 min even with LTTB);
    render_window passes 900 (PNG rendering may span up to the 15-min breath-table
    window). The validator enforces whichever cap is bound."""

    therapy_date: date
    device_id: int | None = None
    session_id: int | None = None  # required when day has >1 session
    offset_start: float = Field(ge=0.0)
    offset_end: float = Field(gt=0.0)
    channels: list[WaveformChannelName] = Field(
        default_factory=list, max_length=len(WaveformChannelName)
    )  # typed + bounded (pass-3 MINOR-1); empty → default set
    max_points: int | None = Field(
        default=None, ge=1, le=1000, description="LTTB cap; None = raw"
    )
    window_cap_seconds: float = Field(
        default=120.0, gt=0.0
    )  # tool-specific cap (above)

    @model_validator(mode="after")
    def validate_request(self) -> "WaveformWindowRequest":
        if self.offset_end <= self.offset_start:
            raise ValueError("offset_end must be > offset_start")
        window_seconds = self.offset_end - self.offset_start
        if window_seconds > self.window_cap_seconds:
            raise ValueError(
                f"Window {window_seconds:.0f} s exceeds the {self.window_cap_seconds:.0f} s "
                "cap for this tool; narrow the window"
            )
        if not self.channels:
            self.channels = [
                WaveformChannelName.FLOW,
                WaveformChannelName.PRESSURE,
                WaveformChannelName.LEAK,
            ]
        self.channels = list(dict.fromkeys(self.channels))  # dedup, order-preserving
        return self
```

### 10. Nightly-aggregation DTOs (seams for get_nightly_summary, single-night + range)

**Mode policy (pass-2 F4', revised per pass-3 IMPORTANT-3, binding):** recovery markers (`is_recovery_breath=True`) are persisted from the **primary mode's** detector run only — the mode chosen explicitly at analysis invocation (PR-A step 4: defaults to `DEFAULT_MODE = "aasm"`, analysis/modes/config.py:95, when included in the requested modes; otherwise caller-supplied, `ValueError` if absent). No other mode's recovery markers are persisted. Every DTO surfacing RERA-derived fields carries the source `primary_mode` — never a silently-picked first dict entry. **Aggregation across runs: FL fields aggregate sessions sharing `AlgorithmIdentity` regardless of run modes; RERA/recovery-derived fields additionally require a uniform `primary_mode` across contributing runs — mixed primary modes → RERA fields `null` + `PRIMARY_MODE_MISMATCH`, with per-session run metadata preserved in `session_coverage`.**

**Coverage policy (pass-2 F4', binding; status precedence is TOTAL per §1 `DayAnalysisStatus`; contribution by data per pass-4 IMPORTANT-3):** aggregates are returned with an explicit non-OK `day_status` plus analyzed/eligible counts and missing/stale session IDs (capability-honest beats refusal). Aggregation contribution is determined by `analyzed_session_count > 0`, never by the enum label: on `PARTIAL` days with `analyzed_session_count > 0`, FL/RERA fields aggregate across the OK sessions only (coverage fields disclose the gaps); on `PARTIAL` days with zero OK sessions (e.g. stale + not-run and no current session — a valid state under the total precedence), and on `NOT_RUN`/`STALE` days, there are no current sessions, so FL/RERA fields are `null` + reason (`ANALYSIS_NOT_RUN`/`ANALYSIS_STALE`). **Exception — cross-version FL aggregation within a day: when OK sessions differ on any `CROSS_VERSION_REFUSAL_KEYS` field (`day_status=MIXED_VERSION`), FL fields are `null` + `ALGO_VERSION_MISMATCH`. Mixing breaths from different algorithm versions into one distribution is always refused.**

```python
class NightlyAnalysisSummary(BaseModel):
    """
    Latest-analysis-run fields for one therapy night.
    Source: latest AnalysisResult per session of the day, ordered
    (created_at DESC, id DESC); multiple sessions aggregate across all OK sessions.
    """

    therapy_date: date
    device_id: int

    # Coverage (pass-2 F4') — replaces the single per-day analysis_status
    day_status: DayAnalysisStatus
    session_coverage: list[SessionCoverage]  # one entry per session of the day
    eligible_session_count: int  # sessions with a Session row
    analyzed_session_count: int  # sessions with analysis_status=OK
    missing_or_stale_session_ids: list[int] = Field(
        default_factory=list
    )  # G2 transparency
    algorithm_identity: AlgorithmIdentity | None  # uniform identity of contributing OK
    # sessions; None when day_status in
    # (NOT_RUN, MIXED_VERSION) or zero OK
    # sessions. Full per-run AlgoVersions
    # (incl. run metadata) lives in
    # session_coverage (pass-3 IMPORTANT-3)

    rera_count: int | None
    rera_reason: NullReason | None  # PRIMARY_MODE_MISMATCH when OK sessions' runs
    # differ in primary_mode (pass-3 IMPORTANT-3)
    primary_mode: str | None  # uniform mode whose recovery markers sourced
    # rera_count; None when mixed (per-session
    # values remain in session_coverage)
    fl_median: float | None  # median mid_insp_flattening (from Breath rows)
    fl_95th: float | None
    fl_max: float | None
    fl_reason: NullReason | None
    # FL aggregation rule: day_status=MIXED_VERSION → fl_median/fl_95th/fl_max = None,
    # fl_reason = NullReason.ALGO_VERSION_MISMATCH. Otherwise aggregate across OK sessions.

    # Compliance (reimplemented — prior calculate_compliance_rate deleted from main)
    total_therapy_hours: float  # from Day.total_therapy_hours
    compliance_threshold_hours: float  # caller-supplied, default 4.0
    is_compliant: bool  # total_therapy_hours >= threshold


class NightlyRangeSummary(BaseModel):
    """
    Compliance + analysis summary over a date range. Consumed by MCP
    get_nightly_summary range mode — keeps aggregation in the service layer (F5 guard).
    """

    date_start: date
    date_end: date
    device_id: int
    compliance_threshold_hours: float
    n_calendar_nights: int  # inclusive count of nights in [date_start, date_end]
    n_nights: int  # nights with ≥1 Session row (nights with data)
    days_compliant: int  # nights where Day.total_therapy_hours >= threshold
    compliance_pct: (
        float  # days_compliant / n_calendar_nights * 100; 0.0 when range empty.
    )
    # Insurance-style: a night with no usage is non-compliant.
    nights: list[NightlyAnalysisSummary]  # ordered by date ascending; data nights only.
    # Per-night coverage/FL/RERA rules exactly per
    # the §10 policies: nights aggregate their OK
    # sessions when analyzed_session_count > 0
    # (gaps disclosed via coverage fields);
    # zero-current nights (NOT_RUN/STALE/zero-current
    # PARTIAL) null + reason;
    # MIXED_VERSION nights null FL + ALGO_VERSION_MISMATCH.
```

### 11. Capabilities seam

```python
class DeviceCapabilities(BaseModel):
    """
    What data is actually present for a device over a (requested) date range —
    derived entirely from DB rows; parser metadata is supplementary context only.
    Service-layer source for docs://capabilities and the per-response
    device_capabilities block (G2).
    Column refs: Waveform.waveform_type (models.py:268), Setting.key (models.py:398).
    """

    device_id: int

    # Requested range (always echoes caller input)
    requested_date_start: date | None  # None = "full imported range" query
    requested_date_end: date | None

    # Actual covered range (nullable when no data exists in the requested range)
    actual_date_start: date | None  # earliest Day.date with ≥1 session
    actual_date_end: date | None  # latest Day.date with ≥1 session
    null_reason: NullReason | None  # NO_DATA_IN_RANGE when actual_* are None

    # Content (empty lists — not null — when range has data but a category is absent)
    channels_present: list[
        str
    ]  # distinct Waveform.waveform_type values in actual range
    all_setting_keys_present: list[
        str
    ]  # ALL distinct Setting.key values in actual range
    rx_keys_present: list[str]  # subset: RX_KEYS found non-null in ≥1 session
    # (RX_KEYS = settings-timeline subset, not full capability)
    event_types_present: list[str]  # distinct Event.event_type values in actual range
    session_count: int  # total Session rows in actual range
    nights_with_data: int  # distinct Day.date values with ≥1 session
    supported_vendor_models: list[str]  # from parser registry — supplementary only
```

### 12. CA-analysis DTOs

```python
class CaDetail(BaseModel):
    """Per-CA event analysis."""

    session_id: int
    session_start_wall_clock: datetime  # naive — tier-2 anchor
    timezone_status: TimezoneStatus = TimezoneStatus.UNKNOWN
    offset_seconds: float  # CA start, from session start
    duration_seconds: float | None
    # Derived via the WaveformWindow seam:
    preceding_mv_slope: float | None  # L/min per minute over 120 s before CA
    preceding_mv_reason: NullReason | None
    ps_delivered_cmh2o: float | None  # pressure support at CA time
    ps_reason: NullReason | None
    stability_index: float | None  # CV of MV in 60 s before CA
    stability_reason: NullReason | None


class CaAnalysisResult(BaseModel):
    """Result of get_ca_analysis()."""

    query_date: date
    device_id: int
    day_status: DayAnalysisStatus  # coverage policy, §10 (pass-2 F4')
    session_coverage: list[SessionCoverage] = Field(default_factory=list)
    algorithm_identity: AlgorithmIdentity | None  # uniform identity of contributing OK
    # sessions; None when day_status in
    # (NOT_RUN, MIXED_VERSION). Per-run
    # AlgoVersions in session_coverage (§1)
    null_reason: NullReason | None
    ca_events: list[CaDetail]
    # CA events are event-anchored (from Event rows), so day_status=PARTIAL,
    # MIXED_VERSION, or even NOT_RUN does not refuse them (consistent with §6
    # CA_CENTERED, pass-3 IMPORTANT-5); night-level fields below aggregate only
    # OK sessions and are null + reason when coverage is insufficient.
    # Night-level (from AnalysisResult periodic_breathing / csr_detection dicts):
    periodic_breathing_pct: float | None
    pb_reason: NullReason | None
    mv_rolling_variance: float | None  # variance of 10-min MV bins across night
    mv_variance_reason: NullReason | None
```

MV-derived metrics (`preceding_mv_slope`, `stability_index`, `mv_rolling_variance`) prefer a device-recorded MV waveform when one exists. When a session has no device MV channel, `get_ca_analysis` derives MV at query time from the flow waveform (trailing 60-s mean of positive-clipped flow, sampled every 2 s) and computes the same metrics from it — labeled, never silent. Provenance is surfaced as `mv_source` (`"device"` / `"flow_derived"` per CA event; `"device"` / `"flow_derived"` / `"mixed"` / null at night level) alongside `mv_fallback_version` (`MV_FALLBACK_ALGO_VERSION`), so consumers can distinguish measured from derived ventilation.

### 13. `BreathService` — public method signatures

```python
class BreathService:
    """Query layer over the breaths table. All methods are async."""

    def __init__(self, db_session: AsyncSession) -> None: ...

    async def get_breath_table(self, query: BreathQueryRange) -> BreathPage:
        """Raw or binned breath fetch. Ordering ascending (session_id, breath_number).
        Latest analysis run per session selected by (created_at DESC, id DESC) —
        the binding deterministic selector (PR-A step 1); never bare MAX(created_at).
        analysis_status=NOT_RUN (null rows/bins) when no AnalysisResult exists;
        STALE_VERSION when engine_versions_json differs from _current_algorithm_identity()."""

    async def find_windows(
        self,
        therapy_date: date,
        criterion: WindowCriterion,
        n: int,
        options: WindowCriterionOptions | None = None,
        device_id: int | None = None,
    ) -> FindWindowsResult:
        """N windows matching criterion, worst first, built per the §6 construction
        rule. Dedup: >50% overlap merges, keep worst. Criterion-irrelevant non-default
        options → ValueError naming the fields. FL-ranked criteria refuse on
        day_status=MIXED_VERSION; FL_RUN_ENDING_IN_RECOVERY additionally refuses on
        mixed primary_mode (PRIMARY_MODE_MISMATCH); CA_CENTERED proceeds on any
        day_status with nullable per-window analysis provenance (§6)."""

    async def compare_epochs(
        self,
        epochs: list[EpochRequest],
        metrics: list[DistributionMetric] | None = None,  # None = all
    ) -> CompareEpochsResult:
        """Distributions across RxTracker epochs. Refuses on any
        CROSS_VERSION_REFUSAL_KEYS mismatch (ALGO_VERSION_MISMATCH, §1/§7) or
        mid-epoch RX change (RX_CHANGED_WITHIN_EPOCH + rx_violations); mixed
        primary modes degrade RERA fields only (PRIMARY_MODE_MISMATCH, §7)."""

    async def get_analysis_status(
        self, session_id: int
    ) -> tuple[AnalysisStatus, AlgoVersions | None]:
        """(status, versions) for a session's latest AnalysisResult.
        (NOT_RUN, None) when absent; (STALE_VERSION, versions) on version drift."""

    async def get_nightly_summary(
        self,
        therapy_date: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyAnalysisSummary:
        """Latest-run analysis fields aggregated across all OK sessions of a day,
        with per-session coverage + day_status per the §10 coverage policy.
        FL median/95th/max computed from Breath rows of the latest runs;
        MIXED_VERSION days refuse FL aggregation (ALGO_VERSION_MISMATCH)."""

    async def get_nightly_range_summary(
        self,
        date_start: date,
        date_end: date,
        device_id: int | None = None,
        compliance_threshold_hours: float = 4.0,
    ) -> NightlyRangeSummary:
        """Per-night summaries + aggregate compliance. compliance_pct uses the
        calendar-night denominator. Per-night FL/RERA fields follow the §10
        coverage policy exactly (pass-3 IMPORTANT-3 doc alignment; contribution
        by data per pass-4 IMPORTANT-3): nights with analyzed_session_count > 0
        aggregate their OK sessions with gaps disclosed via coverage fields;
        zero-current nights (NOT_RUN, STALE, and PARTIAL with zero OK sessions)
        carry null FL/RERA + reason; MIXED_VERSION nights null FL +
        ALGO_VERSION_MISMATCH. Device auto-select per BreathQueryRange rule."""

    async def get_device_capabilities(
        self,
        device_id: int,
        date_start: date | None = None,  # None = full imported range
        date_end: date | None = None,
    ) -> DeviceCapabilities:
        """Actual covered range + channels (Waveform.waveform_type), event types,
        setting keys present. No sessions in range → null actual endpoints +
        null_reason=NO_DATA_IN_RANGE; data present but category absent → empty lists.
        all_setting_keys_present reports ALL distinct Setting.key values (models.py:398);
        rx_keys_present is the RX_KEYS subset. Calls ensure_registered_parsers()
        (§15) before querying the parser registry."""

    async def get_contextual_events(
        self,
        therapy_date: date,
        event_types: list[str] | None = None,
        min_duration: float | None = None,
        device_id: int | None = None,
    ) -> list[ContextualEvent]:
        """Machine events enriched with waveform context via the WaveformWindow seam."""

    async def get_waveform_window(
        self, request: WaveformWindowRequest
    ) -> WaveformWindow:
        """Convenience orchestrator over the §9 split seam: awaits
        fetch_waveform_window_raw(self._db, request) on the injected session —
        NEVER closes it (pass-3 IMPORTANT-4) — then returns
        compute_waveform_window(raw). Callers wanting compute outside the DB
        scope (MCP render/raw tools SHOULD) call the two §9 functions directly
        around their own scope boundary. Missing channels listed with
        missing_channel_reason=CHANNEL_ABSENT (None when nothing is missing)."""

    async def get_ca_analysis(
        self, therapy_date: date, device_id: int | None = None
    ) -> CaAnalysisResult:
        """Per-CA context + night-level periodic-breathing stats from
        AnalysisResult.programmatic_result_json."""

    @staticmethod
    def _current_algorithm_identity() -> AlgorithmIdentity:
        """Current algorithm identity constants for STALE_VERSION detection. Sources:
        segmenter/fl_classifier version strings (PR-A adds), FLATTENING_ALGO_VERSION,
        TRIGGER_CYCLE_ALGO_VERSION, LEAK_VALID_ALGO, and RECOVERY_DETECTOR_ALGO_VERSION
        constants in analysis/shared/ (PR-A adds). Compares AlgorithmIdentity
        structurally — run metadata (primary_mode, modes) is a different type and
        cannot enter the comparison (§1, pass-3 IMPORTANT-2/-3)."""
```

### 14. Design notes for PR-B (Duncan)

1. **`WaveformService.get_waveform_data()` closes its session** after the I/O phase (waveform_service.py:115). PR-B tools needing multi-channel data use the §9 split seam — `fetch_waveform_window_raw()` inside the scope, `compute_waveform_window()` after it closes (or the `get_waveform_window()` orchestrator when compute-inside-scope is acceptable) — never `WaveformService` directly.
2. **Compliance calc**: `calculate_compliance_rate` / `COMPLIANCE_MIN_HOURS` were deleted from main with the old server. `NightlyAnalysisSummary.is_compliant` computes from `Day.total_therapy_hours` vs caller threshold (default 4.0 h); range mode uses the calendar-night denominator in `NightlyRangeSummary`.
3. **Version guard**: `compare_epochs` refuses on any `CROSS_VERSION_REFUSAL_KEYS` mismatch (§1: `format_version`/`segmenter`/`fl_classifier`/`flattening`/`leak_valid`/`recovery_detector`) — not `trigger_cycle` or run metadata (`primary_mode`/`modes`); mixed primary modes degrade RERA fields only (§7).
4. **Session pattern**: `BreathService.__init__` takes an `AsyncSession`, matching every existing service. MCP tools obtain sessions via the scope-provider seam the plan defines (M2); `BreathService` is identical under either provider.
5. **`engine_versions_json` shape (binding, pass-4 IMPORTANT-5)**: the stored JSON is the nested structural split — exactly:
   ```python
   engine_versions_json = {
       "identity": algorithm_identity.model_dump(),  # AlgorithmIdentity (§1)
       "run": run_metadata.model_dump(),  # AnalysisRunMetadata (§1)
   }
   ```
   `store_result()` (analysis/service.py:625) is the sole write point and writes this shape; it validates as the §1 `AlgoVersions` composition. No flat keys are added alongside the legacy `format_version`/`modes` layout. Legacy flat rows read as `STALE_VERSION` — the feature already mandates fresh-DB/reimport, so no conversion code is written.
6. **Error mapping**: `MultiSessionAmbiguityError` → structured MCP error listing `SessionSummary` entries so the caller re-issues with `session_id`.

### 15. `ensure_registered_parsers()` — idempotent parser registration (pass-2 MINOR-3)

`register_all_parsers()` is not cleanly idempotent: `ParserRegistry.register()` raises on duplicate parser IDs (registry.py:62-64) and `register_all_parsers()` logs that as an error (register_all.py:33) — repeated calls preserve state but emit false error logs. PR-A adds a new seam to `src/snore/parsers/register_all.py`:

```python
def ensure_registered_parsers() -> None:
    """
    Register any parser IDs not yet in the global registry. Safe to call
    repeatedly: existing IDs are read via the PUBLIC parser_registry.list_parsers()
    (never the private _parsers attribute) and already-registered parsers are
    skipped — no duplicate-ID errors, no false error logs. Handles partially
    populated registries correctly.

    Call sites: BreathService.get_device_capabilities() and the
    docs://capabilities lifespan hook — both may run in cold-start processes.

    Sketch:
        existing_ids = {p.parser_id for p in parser_registry.list_parsers()}
        for factory in _KNOWN_PARSER_FACTORIES:
            try:
                candidate = factory()
                if candidate.parser_id not in existing_ids:
                    parser_registry.register(candidate)
            except Exception:
                logger.warning("Parser registration skipped", exc_info=True)

    _KNOWN_PARSER_FACTORIES is the parser list register_all_parsers() iterates,
    extracted to a shared module constant so both functions stay in sync.
    register_all_parsers() itself is unchanged (backward compatibility);
    ensure_registered_parsers() is the preferred call in idempotent contexts.
    """
```
