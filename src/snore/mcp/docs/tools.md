# SNORE MCP Tools

SNORE MCP server provides LLM-accessible tools for PAP therapy data analysis.
All tools are **stateless service-layer calls** — they never store state between calls.

## General Information

### Date Format
All `date`, `start`, `end` parameters accept **YYYY-MM-DD** format only.
Example: `"2025-08-01"`.

### Null fields and reasons
When a data field is absent (device does not record it, analysis has not been run, etc.)
the field is `null` and a companion `*_reason` field explains why.
Example: `"rera_index": null, "rera_index_reason": "analysis_not_run"`.

### Device capabilities block
Most tools return a `device_capabilities` block declaring what the device/dataset
actually provides for the queried range.  Do not assume a channel is present — always
check this block before interpreting a null value.

### Clinical profiles
The server is configured with a clinical profile (`neutral` by default).  Profiles
shape the instructions and priority hints only — tools always return the same data
regardless of profile.  To change the active profile, restart the server with
`snore mcp --profile <name>`.  Available profiles: `neutral`, `uars`, `osa`, `csa`.

## Recommended Workflow

1. **Orient** — call `get_data_overview` to discover devices, date ranges, and channels.
2. **Summarize** — call `get_nightly_summary` over a range to identify nights of interest.
3. **Settings** — call `get_settings_timeline` to understand settings epochs.
4. **Events** — call `get_events` on a specific date for event-level detail.
5. (Phase 2+) `get_breath_table`, `find_windows`, `compare_epochs` for flow morphology tuning.
6. (Phase 3+) `render_window`, `get_waveform` for visual inspection and raw escape hatch.

## Tools

---

### get_data_overview

Cold-start orientation tool.  Call this first to discover what is imported.

**Parameters:** none

**Returns:**
- `devices` — list of devices with id, manufacturer, model, date range, session count, therapy modes
- `date_range_start` / `date_range_end` — full imported date range (all devices)
- `total_sessions` — total enabled session count
- `available_waveform_channels` — list of waveform channel names present in any session
- `available_event_types` — list of event type codes present (e.g. `["CA", "H", "OA", "RERA"]`)
- `analysis_run` — whether any analysis results exist
- `analysis_session_count` — number of sessions with analysis results

---

### get_settings_timeline

Returns therapy settings epochs — contiguous periods with identical settings.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| start | str (YYYY-MM-DD) | Yes | Start of date range |
| end | str (YYYY-MM-DD) | Yes | End of date range |
| device_id | int | No | Filter to a specific device |

**Returns:**
- `epochs` — list of `SettingsEpoch` objects
  - `start_date`, `end_date`, `nights` — epoch span
  - `settings` — dict of setting keys (mode, epr_level, epr_mode, pressure_min, pressure_max, pressure_fixed, ipap, epap, ps); absent keys are `null`
  - `changed_keys` — which keys changed vs. previous epoch
  - `device_id`

---

### get_nightly_summary

Per-night therapy summary for a date range.  Paginated (~30 nights/call).

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| start | str (YYYY-MM-DD) | Yes | Start of date range |
| end | str (YYYY-MM-DD) | Yes | End of date range |
| device_id | int | No | Filter to a specific device |
| page | int | No | Page number (1-based, default 1) |
| page_size | int | No | Results per page (default 30, max 90) |
| compliance_threshold_hours | float | No | Compliance threshold in hours (default 4.0) |

**Returns:** `NightlySummaryResponse`
- `nights` — list of `NightlyRow` with per-night metrics
  - `date`, `usage_hours`, `session_count`
  - `ahi`, `oai`, `cai`, `hi` (events/hr) — null if not computed
  - `rera_index` (events/hr), `rdi` — null + `rera_index_reason: "analysis_not_run"` if analysis absent
  - Pressure: `pressure_median_cmh2o`, `pressure_95th_cmh2o`, `epap_median_cmh2o`
  - Leak: `leak_median_lpm`, `leak_95th_lpm`
  - Resp: `rr_mean_bpm`, `tv_mean_ml`, `mv_mean_lpm`
  - SpO₂: `spo2_mean_pct`
- `compliance` — present in range mode: `threshold_hours`, `days_compliant`, `days_total`, `compliance_pct`

---

### get_events

Respiratory events for a single session date with per-event context.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| date | str (YYYY-MM-DD) | Yes | Session date |
| types | list[str] | No | Event type filter (e.g. `["CA", "OA"]`) |
| min_duration | float | No | Minimum event duration in seconds |
| include_context | bool | No | Attach per-event context block (default true) |

**Returns:** `EventsResponse`
- `events` — list of `EventRow`
  - `id`, `event_type`, `start_time_iso`, `duration_seconds`
  - `spo2_drop_pct`, `peak_flow_limitation`
  - `context` — `minutes_since_session_start` (pressure/leak/MV context in Phase 4)

**Common event_type values:** `OA` (obstructive apnea), `CA` (central apnea),
`H` (hypopnea), `RERA`, `FL` (flow limitation), `VS` (vibratory snore).
