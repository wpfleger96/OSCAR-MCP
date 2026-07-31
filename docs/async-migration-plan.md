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
  `Day.import_date`, model `created_at`/`updated_at` pairs,
  `AnalysisResult.created_at`
- **Naive by contract (device/session wall-clock — no source timezone, never
  convert):** `Session.start_time`/`end_time`, `Event.start_time`,
  `AnalysisResult.timestamp_start`/`timestamp_end`, `DetectedPattern.start_time`

`cache_ok = True` set on the type. Existing naive UTC values backfill correctly
(SQLite stores them without offset; `UTCDateTime` reads them back as UTC-aware).

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

### 6. Transaction ownership

One authoritative table documenting who opens, commits, and rolls back each mutating
entry point.

Import UoW: batch-scoped, `ImportService`-owned session; per-session
`begin_nested()` savepoints so one failed import cannot poison the batch. One
forced-failure test asserts zero rows survive when a session import raises mid-batch.

### 7. I/O–compute DTO split

No ORM session crosses a thread boundary. All five surfaces (single analysis, batch
analysis, waveform, report, export/import) use detached DTOs between I/O and compute
phases.

Batch analysis: `analyze_one` splits into a read+compute `session_scope()` (returns a
detached `AnalysisResult`) and a separate write-only `session_scope()` (INSERT only).
This eliminates SQLite write-lock contention under `ThreadPoolExecutor` with
`autocommit=False`.

A narrow coordinator interface (`submit / progress / cancel`) backs the scheduler so
PR-2 can swap its internals to `asyncio` tasks without touching callers.

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
