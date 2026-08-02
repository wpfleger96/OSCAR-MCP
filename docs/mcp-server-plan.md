# SNORE MCP Server Plan — v3.3

MCP as a third thin presentation layer over the existing async service layer, peer of the
CLI and FastAPI backend. Purpose-built for LLM-assisted PAP settings tuning but designed
with generic contracts suitable for any PAP dataset.

---

## Context

SNORE started life as OSCAR-MCP; commit `dd08225` deliberately removed a 762-line FastMCP
server (8 tools, 3 resources) with intent to re-add later. This plan re-adds MCP as a
**third thin presentation layer** over the existing async service layer — never a place
where analysis logic lives.

---

## Design Doctrine

**Tiered data access — computed metrics primary, rendered PNG charts secondary, raw arrays
tertiary and tightly capped.** Progressive disclosure: overview → summary → events → breath
table → raw waveform. Raw data is never the entry point.

- **Compute server-side, return compact JSON.** Units on every field. Timestamps ISO 8601
  with explicit offset.
- **Data-quality flags everywhere**: per-window/per-breath `leak_valid`, `mask_off`,
  `ramp_active` so junk is excludable automatically.
- **Algorithm versioning in output** (`fl_algo: "v1.2"`) so epoch comparisons never
  silently span algorithm changes.

## Genericity Principles

- **G1 — Profile-parameterized, neutral by default.** Clinical emphasis lives in named
  profiles, not in tool design. No tool returns different data per profile.
- **G2 — Capability-honest.** Absent data is `null` + reason, never fabricated.
- **G3 — Stateless and scope-ready.** No module-global state; DB via lifespan-provided
  session factory; explicit ranges/filters in every tool.
- **G4 — Vendor dispatch stays in the parser/service layer.**

---

## Locked Decisions

1. **Transport: stdio now, HTTP-ready by construction.** `snore mcp` (stdio). FastMCP
   serves stdio and streamable-HTTP from the same tool definitions.
2. **Full analysis at import time as async background job.** Import commits first,
   analysis follows without blocking ingestion. On-demand `snore analysis run` stays.
3. **No Alembic migrations — ever.** Fresh DBs get the right schema via `create_all`.
   Pre-existing DBs: breath-backed tools return capability-honest error → drop + reimport.
4. **`matplotlib` as a main dependency** when `render_window` lands (Phase 2/PR-B Stage 3).
5. **Tools are `async def`** calling the async service layer natively.

---

## 2-PR Structure

### PR-A — Substrate (@Hayt, merges first)
`Breath` model, import-time background analysis, breath persistence, `breath_service.py`.
Zero MCP knowledge. Branch: `will/import-time-analysis`.

**Boundary:** `database/models.py`, `analysis/**`, `services/**` (incl. new
`breath_service.py`), `api/**`, `cli/commands/import_data.py`, tests.

### PR-B — Complete MCP layer (@Duncan, merges second)
`src/snore/mcp/` package, all ~10 tools across Stages 1–3, resources, profiles, CLI entry,
`fastmcp`/`matplotlib` deps, this doc. Branch: `will/mcp-server`.

**Boundary:** `src/snore/mcp/**`, `cli/commands/mcp.py`, `cli/__init__.py` (register only),
`pyproject.toml`, `docs/mcp-server-plan.md`.

**Staged internally:**
- Stage 1 (no PR-A dependency): skeleton + free tools (`get_data_overview`,
  `get_settings_timeline`, `get_nightly_summary`, `get_events`) + resources + profiles.
- Stage 2 (after PR-A merges + rebase): `get_breath_table`, `find_windows`,
  `compare_epochs` (via `breath_service`).
- Stage 3 (same PR): `render_window` (matplotlib), `get_waveform` (LTTB),
  `get_ca_analysis`.

---

## Tools (current — Stage 1)

### get_data_overview
Cold-start orientation. Call first. Returns devices, date ranges, available waveform
channels, event types, analysis status.

### get_settings_timeline(start, end, device_id?)
Therapy settings epochs. Generic `RX_KEYS` only. Changed keys flagged per epoch.

### get_nightly_summary(start, end, device_id?, page, page_size, compliance_threshold_hours)
Per-night summary, paginated. Analysis-derived fields (RERA index, RDI) are `null` +
`analysis_not_run` when analysis absent. Compliance block in range mode.

### get_events(date, types?, min_duration?, include_context)
Respiratory events for a session date. Inline context: minutes since session start.

---

## Resources

- `docs://tools` — complete tool reference (this package's `docs/tools.md`)
- `docs://schemas/{type}` — JSON schema for any named Pydantic response type
- `docs://capabilities` — dynamically generated from imported data

---

## Clinical Profiles

Profiles shape the INSTRUCTIONS resource and priority hints only (G1). No tool returns
different data per profile. Available: `neutral` (default), `uars`, `osa`, `csa`.

---

## Implementation Conventions

- `fastmcp>=3` standalone (NOT `mcp[cli]`)
- `tool_error_boundary` on every tool
- `RESPONSE_SIZE_LIMIT` guard (500 KB); returns narrow-your-query guidance
- `docs://tools` resource + REQUIRED-READING preamble
- Lifespan resolves DB via `DatabaseTarget.from_env_and_flags`
- `session_scope()` per tool call — no module-global state

---

## Success Criteria

Claude Desktop/Code connects over stdio and can, in one conversation: orient via
`get_data_overview` → identify worst flow-limitation windows → pull the breath table for
one → view the PNG → compare two settings epochs with real distribution stats — using only
generic tool contracts, with `clinical_profile: uars` active. Stage 1 covers the
orientation and summary half of this workflow; Stages 2–3 complete it.
