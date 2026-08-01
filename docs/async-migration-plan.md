# SNORE Async Migration Plan

Two-PR sequence that migrates the application from synchronous SQLAlchemy to fully
async `AsyncSession`/`aiosqlite`, while keeping the existing service layer, tests, and
behaviour unchanged.

---

## Why two PRs?

A single PR collapsing sync prep + async flip makes regressions undiagnosable — a
failure could be a query rewrite, a transaction boundary change, or the async
conversion itself. Two PRs give a verified sync baseline (PR-1 tests green) before
the async flip (PR-2) touches a single `async def`.

---

## PR-1 — Sync prep (this PR)

**Goal:** make the codebase async-ready without adding any `async` keyword.

### 1. SQLAlchemy 2.0 `select()` modernisation + `lazy="raise"` gate

All `session.query(...)` call sites migrated to `select(...)` + `session.execute()`.
Every ORM relationship explicitly annotated `lazy="raise"` — any implicit N+1 load
raises immediately rather than silently hitting the database.

### 2. `DatabaseTarget` URL resolver

Single source of truth for database URL resolution across the CLI, API server, and
migration tooling.

**Precedence chain** (highest → lowest):

1. `--db` CLI flag / `SNORE_DATABASE_URL` env var (canonical URL)
2. `SNORE_DB_PATH` env var (bare path → `sqlite:///...`)
3. Default path (`~/.snore/snore.db`)

`snore serve --db <path|url>` exports `SNORE_DATABASE_URL` and removes `SNORE_DB_PATH`
so the FastAPI lifespan and any child process always see a canonical URL.

**Dialect support:**

| Dialect | Parsing | Runtime resolution |
|---------|---------|-------------------|
| `sqlite` | ✅ | ✅ (`pysqlite` in PR-1, `aiosqlite` in PR-2) |
| `postgresql` | ✅ (recognised) | ❌ capability-gated (hosted milestone) |
| other | ❌ error at parse | — |

A `postgresql` URL is parsed without error; resolution raises a clear
`RuntimeError("PostgreSQL support requires a driver that is not installed …")` rather
than a crash or false claim of support.

### 3. `UTCDateTime` timezone contract

Custom SQLAlchemy type that stores timezone-aware datetimes as UTC in SQLite and
normalises any aware result to UTC on read.

**Column classification:**

- **`UTCDateTime` (absolute instants):** `Device.first_seen`, `Device.last_import`,
  `Session.import_date`, model `created_at`/`updated_at` pairs,
  `AnalysisResult.created_at`
- **Naive by contract (device/session wall-clock — no source timezone, never
  convert):** `Session.start_time`/`end_time`, `Event.start_time`,
  `AnalysisResult.timestamp_start`/`timestamp_end`, `DetectedPattern.start_time`

`cache_ok = True` set on the type. Existing naive UTC values are read back as
UTC-aware automatically (SQLite stores them without offset; `UTCDateTime` restores
the `tzinfo` on load).  No migration is needed — this project uses drop-and-reimport
for schema changes; a fresh `create_all` + stamp produces the correct schema.

**Dialect behaviour:**

- SQLite: uses `DateTime` (no native TZ); UTC is re-attached on load.
- PostgreSQL: uses `DateTime(timezone=True)` (TIMESTAMP WITH TIME ZONE); the driver
  receives and returns offset-aware datetimes; normalised to UTC on load.

### 4. SQLite connection recipe (one integrated protocol)

`connect_args={"autocommit": False}` (Python ≥ 3.13 modern transaction control) plus
an event listener that:

1. Temporarily sets `dbapi_conn.autocommit = True`
2. Applies PRAGMAs: `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout`,
   `cache_size`, `temp_store`
3. Restores `autocommit = False`

**Why the toggle?** Applying PRAGMAs under `autocommit=False` fails: a new connection
is already inside an implicit transaction, `journal_mode=WAL` raises
`OperationalError`, and `foreign_keys` silently stays 0. Probe-verified on
Python 3.13.9.

`VACUUM` runs on a separate `AUTOCOMMIT` connection — it cannot execute inside a
transaction.

### 5. Maintenance and genericity

- `DatabaseService` raw-SQL stats queries migrated to portable `select(func.count())`
- SQLite-only operations (VACUUM, file backup, file-level reset) behind explicit
  capability gating
- Alembic `env.py`: conditional `render_as_batch` (SQLite only); URL read from
  `DatabaseTarget`, not hardcoded
- `src/snore/database/schema.sql` deleted — zero callers; obsolete SQLite-only subset
  that diverged from the Alembic migration history
- Typed SQL throughout (no `text()` for DML/ORDER BY); offline PostgreSQL compilation
  suite in `tests/unit/test_dialect_compilation.py` validates syntax against the
  PostgreSQL dialect without a running server

> **Dialect compilation disclaimer:** The offline PostgreSQL compilation suite compiles
> audited statements against the SQLAlchemy PostgreSQL dialect.  This catches type
> mismatches and unsupported constructs at test time but is **NOT a substitute for
> end-to-end testing against a real PostgreSQL instance**.  The PostgreSQL code paths
> are capability-gated and will be validated against a real server at the hosted
> milestone before any production PostgreSQL traffic is served.

### 6. Transaction ownership

One authoritative table documenting who opens, commits, and rolls back each mutating
entry point.

| Entry point | Opens session | Commits | Rollback on error | Notes |
|---|---|---|---|---|
| `session_scope()` context manager | Yes | `session.commit()` on exit | `session.rollback()` on exception | All normal service calls |
| `import_sessions_batch` (per batch) | No (caller provides via `db=` parameter) | No (caller owns the outer transaction) | No (caller owns) | Called by `ImportService` which injects the `ImportService`-owned batch session; each individual session wrapped in `begin_nested()` savepoint |
| `_import_single_session` savepoint | `db.begin_nested()` | `sp.commit()` | `sp.rollback()` | Never commits or rolls back the outer session |
| `cleanup_orphaned_records` | No (caller provides) | **Never** | **Never** | Caller owns the transaction; no internal commit |
| `DatabaseService.reset` | No (caller provides via dep) | Yes (intentional, before VACUUM) | No | VACUUM requires committed state; route dep creates a fresh session per request |
| `BatchAnalysisCoordinator.analyze_one` (read) | Yes (via `session_scope()`) | On scope exit | On scope exception | Closed before compute phase begins |
| `BatchAnalysisCoordinator.analyze_one` (write) | Yes (via `session_scope()`) | On scope exit | On scope exception | Short-lived INSERT only; held ≤ write duration |
| `AnalysisFacade.run_analysis` | No (injects caller's session) | No (caller owns) | No (caller owns) | FastAPI dep injects a request-scoped session; `session_scope` closes it on response |
| `AnalysisFacade.delete_analysis` | No (injects caller's session) | No (caller owns) | No (caller owns) | Same as above; bulk DELETE via typed `delete()` |
| `AnalysisFacade.run_batch_analysis` | No (injects caller's session for query) | No | No | Session used only to fetch session IDs; coordinator opens its own scoped sessions per worker |
| `POST /sessions/{id}/analysis` route | Via FastAPI dep (`session_scope`) | On response exit | On exception | `service_dep` creates a `session_scope` per request |
| `DELETE /analysis` route | Via FastAPI dep (`session_scope`) | On response exit | On exception | Same |
| `POST /analysis/batch` route | Via FastAPI dep (`session_scope`) | On response exit | On exception | Coordinator internally opens additional `session_scope` instances per thread |
| `PUT /sessions/{id}` route | Via FastAPI dep (`session_scope`) | On response exit | On exception | Session update via `SessionService` |
| `DELETE /sessions/` route | Via FastAPI dep (`session_scope`) | On response exit | On exception | Bulk session delete via `SessionService.delete_sessions` |
| `POST /import/` worker thread | Via `session_scope` inside `ImportService.import_sources` | Per batch, by `ImportService` (outer) and `import_sessions_batch` (savepoints) | Per batch exception | `ImportService` opens and injects the batch session; `import_sessions_batch` uses `begin_nested()` savepoints only |

Import UoW: `ImportService.import_sources` opens a `session_scope()` per source batch and injects the live session into `SessionImporter.import_sessions_batch` via the `db=` parameter.  `import_sessions_batch` never opens its own `session_scope()` when called from `ImportService`; it uses `begin_nested()` savepoints only.  The forced-failure test asserts zero child rows (device, day, session, waveform, event, statistics) survive when an import raises mid-batch.

### 7. I/O–compute DTO split

No ORM session crosses a thread boundary. Batch analysis uses detached DTOs between
I/O and compute phases.

Batch analysis: `BatchAnalysisCoordinator.analyze_one` splits into:
1. A **read-only** `session_scope()` that calls `load_session_inputs_raw()` — raw blob fetch, no NumPy.  The session is closed before compute begins.
2. A **compute phase** (no session held): `AnalysisService.prepare_inputs()` (deserialisation + artifact detection) then `compute_analysis()`.
3. A **write-only** `session_scope()` (INSERT only), held only for the write duration.

This eliminates SQLite write-lock contention under `ThreadPoolExecutor` with
`autocommit=False`. Real `processing_time_ms` is measured over the read+compute phase
and written with each result.

`BatchAnalysisCoordinator` provides the narrow `submit / progress / cancel` interface
so PR-2 can swap the executor internals (``ThreadPoolExecutor`` → ``asyncio`` tasks)
without touching callers.

**§7 I/O–compute splits in PR-1:** All six named surfaces have explicit I/O–compute
separation in PR-1:

1. **Batch analysis** (`BatchAnalysisCoordinator`): read-only phase (`load_session_inputs_raw`,
   session closed before NumPy), compute phase (`prepare_inputs` + `compute_analysis`, no session),
   write phase (fresh session for INSERT only).
2. **Single-session analysis** (`AnalysisService.analyze_session`): calls `load_session_inputs_raw`
   then `prepare_inputs` then `compute_analysis`; the session is bounded by the request context
   and no NumPy work runs while holding a query lock.
3. **Waveform load** (`load_waveform_from_db`): `fetch_waveform_blob` (I/O, returns raw bytes)
   then `deserialize_waveform_blob` (compute, no session needed).
4. **Report generation** (`ReportService`): `_fetch_summary_data` / `_fetch_comparison_data`
   (I/O, returns plain Python objects) then `_render_summary` / `_render_comparison`
   (pure Jinja2, static methods, no session).
5. **Export** (`ExportService`): `_build_export_rows` generator yields `(session_dict, events, settings)`
   in bounded chunks of `_EXPORT_CHUNK_SIZE` rows using `yield_per()` — no full materialisation
   of all sessions, events, or settings before rendering.
6. **Import** (`import_sessions_batch`): `parse_sessions()` returns a lazy iterator consumed
   in bounded `batch_size` chunks — no full-batch prefetch.

### 8. Import-job state machine

Replaces the destructive shared `Queue`.

**States:** `pending → running → succeeded | failed | cancelled`
`pending → cancelled` is also valid (DELETE can race POST immediately after creation).

**Properties:**

- Start exactly once at POST; subsequent GETs attach an observer, never restart work
- Each observer gets its own **capacity-one coalescing channel** backed by the job's
  latest-progress snapshot — a stalled SSE client never accumulates unbounded events;
  terminal delivery is never dropped
- Late/reconnecting observers immediately receive current progress or terminal state
  (no 404 after job completion)
- Terminal state retained for a documented TTL; reaper removes **terminal jobs only** —
  active jobs are never reaped regardless of age
- Cancellation is idempotent after any terminal state; exactly one immutable terminal
  event under cancel/complete races
- POST failure between temp-dir creation and job registration: immediate directory
  cleanup, no orphan job
- App shutdown cancels then awaits all workers

---

## PR-2 — Atomic async flip (planned)

**Goal:** convert every service, router, and CLI touch-point to `AsyncSession` /
`aiosqlite` in a single, reviewable diff against the PR-1 baseline.

**Dependencies added:** `aiosqlite` (runtime), `pytest-asyncio` (dev).
No Postgres drivers until the hosted milestone.

### Key constraints

**Async SQLAlchemy usage matrix:**

| Operation | Form |
|-----------|------|
| Transaction entry | `async with session.begin():` |
| Nested savepoint | `async with session.begin_nested():` |
| Awaited calls | `execute()`, `get()`, `flush()`, `commit()`, `rollback()`, `delete()`, `close()` |
| Not awaited | `add()`, `add_all()`, `expire()`, post-`execute()` `Result` access |

**Bulk inserts:** waveforms, events, settings use typed `insert()` executemany inside
per-session `begin_nested()` savepoints. `Statistics` stays `add()`.

**SQLite connection recipe** carries over: adapter-safe autocommit toggle around
PRAGMAs on `engine.sync_engine`, same connection tests.

**CLI bridge:** `asyncio.run()` at the `db_session` context-manager seam. Alembic
stays sync (via `run_sync`).

**Exit gate:** full suite green + `just check` + `just web-check`, with a repo search
proving no synchronous application `Session` usage outside intentionally-sync Alembic
code.

---

## After PR-2: MCP server

See `docs/mcp-server-plan.md`. Tools are native-async from day one; no async retrofit
required.

---

## Review provenance

Thufir passes 1–5 (scores 7/6/5 → 7/7/6 → 8/8/7 → 8/8/8 → 9/8/7); all IMPORTANT
findings folded; pass-5 residuals folded as final per Will's option-A ruling. SQLite
connection recipe probe-verified (Python 3.13.9, repo env).
