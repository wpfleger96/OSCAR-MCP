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
actually provides for the queried range.  The block includes `manufacturer`, `model`, and `serial_number` identifying the device.  Do not assume a channel is present — always check this block before interpreting a null value.

### Clinical profiles
The server is configured with a clinical profile (`neutral` by default).  Profiles
shape the instructions and priority hints only — the same data is returned regardless
of clinical profile.  To change the active profile, restart the server with
`snore mcp --profile <name>`.  Available profiles: `neutral`, `uars`, `osa`, `csa`.

### Data profile scoping
All data access is scoped to the active data profile resolved at server startup (the
first non-deleted Profile record in the database).  All tool responses contain only
data belonging to that profile.  In a single-user deployment this is transparent; in
multi-user deployments each server process sees exactly one user's data.

## Recommended Workflow

1. **Orient** — call `get_data_overview` to discover devices, date ranges, and channels.
2. **Summarize** — call `get_nightly_summary` over a range to identify nights of interest.
3. **Settings** — call `get_settings_timeline` to understand settings epochs.
4. **Events** — call `get_events` on a specific date for event-level detail.
5. **Morphology** — call `get_breath_table`, `find_windows`, or `compare_epochs` for breath-level flow morphology tuning.
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
  - `device_id` — `int | null`; null when no device is associated with the epoch (no `0` sentinel is used to represent an unknown device)
  - `device_capabilities` — device/dataset capability block for this epoch's date range
- `total_epochs`

---

### get_nightly_summary

Per-night therapy summary for a date range.  Paginated (30 nights/page by default).
Date ranges over 90 calendar nights are rejected with a tool error — page across
multiple calls for longer ranges.

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
  - `rera_index` (events/hr) — null + `rera_index_reason` when absent; reason values include `"analysis_not_run"` (breath analysis has not been executed for this night) and `"duration_zero"` (a RERA count exists but total therapy hours is 0, making the per-hour index undefined)
  - `rdi` (events/hr) = AHI + RERA-proxy index — null + `rdi_reason` when absent; same reason values as `rera_index_reason`
  - `fl_median`, `fl_p95`, `fl_max` — breath-level flow-limitation stats — null + `*_reason`
  - `rera_proxy_count` — RERA-proxy breath count — null + `rera_proxy_reason`
  - `ti_median_s` — median inspiratory time in seconds — null + `ti_median_reason`
  - `ie_ratio` — median I:E ratio over leak-valid breaths — null + `ie_ratio_reason`
  - Pressure: `pressure_median_cmh2o`, `pressure_95th_cmh2o`, `epap_median_cmh2o`
  - Leak: `leak_median_lpm`, `leak_95th_lpm`
  - Resp: `rr_mean_bpm`, `tv_mean_ml`, `mv_mean_lpm`
  - SpO₂: `spo2_mean_pct`
- `compliance` — present whenever `start != end` (range mode), even when the range contains no night data rows; null only for single-date requests. Fields: `threshold_hours`, `days_compliant`, `days_total` (CALENDAR nights in the requested range; nights without data count as non-compliant), `compliance_pct`
- `device_capabilities` — device/dataset capability block for the queried range; null when device is ambiguous

**Error conditions:**
- Date range over 90 calendar nights → tool error; make multiple calls with shorter ranges.
- Multiple devices have data for the range and no `device_id` given → tool error; add `device_id`.

---

### get_events

Respiratory events for a single session date with per-event context.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| date | str (YYYY-MM-DD) | Yes | Session date |
| device_id | int | No | Filter to a specific device. Required when multiple devices have data for the same date. |
| types | list[str] | No | Event type filter (e.g. `["CA", "OA"]`) |
| min_duration | float | No | Minimum event duration in seconds |
| include_context | bool | No | Attach per-event context block (default true) |
| max_events | int | No | Maximum events to return after filtering (default 500, minimum 1). Truncation is applied after type and duration filters. |

**Returns:** `EventsResponse`
- `date` — queried session date
- `session_id` — response-level session anchor; null when no events were returned or events span multiple sessions
- `session_start_wall_clock` — response-level session anchor; null when no events were returned or events span multiple sessions
- `timezone_status` — always `"unknown"` (device wall-clock; no TZ is recorded)
- `events` — list of `EventRow`
  - `session_id`, `session_start_wall_clock` — per-event session anchors; always populated on each row
  - `event_type`, `start_time_wall_clock`, `timezone_status`
  - `offset_seconds` — seconds from *this event's own* `session_start_wall_clock` (correct on multi-session nights)
  - `duration_seconds`, `spo2_drop_pct`, `peak_flow_limitation`
  - `pressure_reason`, `leak_reason`, `mv_reason` — explain null context values when waveform data is absent
  - `context` (`EventContext`) — attached when `include_context` is true:
    `pressure_at_event_cmh2o`, `leak_at_event_lpm`, `mv_prior_120s_lpm`, `minutes_since_session_start`
- `total_events` — untruncated count of events matching the filters (before `max_events` truncation)
- `truncated` — `true` when the events list was cut at `max_events`; `total_events` still reflects the full count
- `device_capabilities` — device/dataset capability block for the queried date; null when no events were found for the date and no `device_id` argument was provided

**Error conditions:**
- Date with no therapy data → tool error; use `get_data_overview` to find imported dates.
- Multiple devices have data for the date and no `device_id` given → tool error; add `device_id`.
- Date with therapy data but zero events matching the filters → empty `events` list with null response-level anchors (not an error).

**Common event_type values:** `OA` (obstructive apnea), `CA` (central apnea),
`H` (hypopnea), `RERA`, `FL` (flow limitation), `VS` (vibratory snore).

---

### get_breath_table

Paginated breath-level table for a single therapy night.  Use to inspect individual
breath features (flow class, flattening index, timing, tidal volume) within a time
window.  Requires breath-level analysis results (`get_data_overview` →
`analysis_run: true`).

**Raw windows are capped at 15 minutes** (`offset_end - offset_start ≤ 900 s`).
For longer windows, set `bin_minutes` to aggregate into time bins — the response then
populates `bins` instead of `rows`.  `page_size` is capped at 2000 rows per page.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| date | str (YYYY-MM-DD) | Yes | Session date |
| offset_start | float | Yes | Window start in seconds from session start (≥ 0) |
| offset_end | float | Yes | Window end in seconds (> offset_start; raw window ≤ 900 s unless bin_minutes set) |
| device_id | int | No | Filter to a specific device; required when multiple devices share the date |
| session_id | int | No | Filter to a specific session; required when the device had multiple sessions that day |
| page | int | No | Page number for raw rows (1-based, default 1) |
| page_size | int | No | Rows per page for raw fetch (default 500, max 2000) |
| bin_minutes | float | No | Aggregate into bins of this width (min 1.0); required for windows > 15 min |

**Returns:** `BreathTableResponse`
- `query` — echo of the resolved query parameters
- `session_id`, `session_start_wall_clock`, `timezone_status` — response-level session anchor (tier-2 wall-clock; `timezone_status` always `"unknown"`)
- `analysis_status` — `"ok"`, `"not_run"`, or `"stale"`
- `algo_versions` — algorithm identity and run metadata; null when analysis not run
- `null_reason` — explains absent data; `"analysis_not_run"` or `"analysis_stale"`
- `is_binned` — true when `bin_minutes` was set; `rows` or `bins` is populated, never both
- `total_breaths`, `page`, `page_size` — pagination metadata
- `rows` — list of `BreathTableRow` (raw mode): per-breath offsets, timing, amplitude, flow class, quality flags
- `bins` — list of `BreathTableBin` (binned mode): aggregated medians/modes per time bin
- `device_capabilities` — device/dataset capability block for the queried date

**Error conditions:**
- No sessions found for date → tool error; use `get_data_overview` to check imported dates.
- Multiple devices on date and no `device_id` → tool error listing device IDs; add `device_id`.
- Multiple sessions on date and no `session_id` → tool error listing session IDs; add `session_id`.
- Raw window > 15 min without `bin_minutes` → tool error; set `bin_minutes` to aggregate.
- Breath-level data tables missing → tool error; run `snore analysis run`.

---

### find_windows

Find the N worst breath windows matching a flow-limitation criterion for a single
therapy night.  Use to locate specific regions worth reviewing in `get_breath_table`
or (Phase 3) `render_window`.

**Window construction:** For each candidate anchor breath, a context window is built
using the configured `context_breaths_before` / `context_breaths_after` /
`context_seconds` bounds.  Windows with overlapping regions >50% of the shorter
window's length are deduped, keeping the worst-ranked.  Results are returned
ordered worst-first.

**Epoch contribution:** Only OK-analysis sessions contribute window candidates.
Sessions with `not_run` or `stale` analysis are skipped; their status appears in
`session_coverage`.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| date | str (YYYY-MM-DD) | Yes | Session date |
| criterion | str | Yes | Selection criterion (see below) |
| n | int | No | Number of windows to return (1–50, default 5) |
| device_id | int | No | Filter to a specific device; required when multiple devices share the date |
| include_unknown_leak | bool | No | Include breaths with unknown leak validity (default false); only for `worst_flattening_leak_valid` |
| flattening_threshold | float | No | Minimum mid-inspiratory flattening to anchor a window; service default when omitted |
| min_window_breaths | int | No | Minimum breaths per window (default 3) |
| context_breaths_before | int | No | Context breaths before the anchor (default 3) |
| context_breaths_after | int | No | Context breaths after the anchor (default 3) |
| context_seconds | float | No | Context window duration in seconds (default 120.0); only for `ca_centered` |
| min_fl_run_length | int | No | Minimum FL-class run length (default 2); only for `fl_run_ending_in_recovery` |
| fl_class_threshold | int | No | Minimum flow class to count as FL (default 4); only for `fl_run_ending_in_recovery` |

**Valid criteria:**
- `"worst_flattening_leak_valid"` — windows ranked by worst mean mid-inspiratory
  flattening among leak-valid breaths; best for locating FL hotspots.
- `"ca_centered"` — context window centred on each CA event; works even when the
  day mixes algorithm versions (no cross-version refusal for this criterion).
- `"fl_run_ending_in_recovery"` — FL runs immediately followed by a recovery
  breath; requires uniform primary_mode across all sessions of the day.

**Returns:** `FindWindowsResponse`
- `query_date` — queried date in YYYY-MM-DD
- `device_id` — resolved device ID; null when no sessions found (service uses 0 as a no-device sentinel — the MCP layer never emits 0)
- `criterion` — echoed criterion string
- `day_status` — `"ok"`, `"partial"`, `"mixed_version"`, `"not_run"`, or `"stale"`
- `session_coverage` — per-session analysis status list
- `algorithm_identity` — shared algorithm identity across sessions; null on mixed-version days
- `null_reason` — present when `windows` is empty due to a refusal
- `primary_mode` — present only for `fl_run_ending_in_recovery` when mode is uniform
- `windows` — list of `WindowRow`, worst-first
- `device_capabilities` — device/dataset capability block

**Refusal semantics (successful responses with empty `windows`):**
- `null_reason: "algo_version_mismatch"` — the day has sessions analysed with different
  algorithm versions.  FL-ranked criteria (`worst_flattening_leak_valid` and
  `fl_run_ending_in_recovery`) refuse comparison; `ca_centered` is unaffected and
  continues to work normally, even with no analysis run on some sessions.
- `null_reason: "primary_mode_mismatch"` — sessions differ in primary mode; only
  `fl_run_ending_in_recovery` refuses; other criteria are unaffected.
- `null_reason: "analysis_not_run"` — no analysis results exist for this date.

**Error conditions:**
- Unknown `criterion` → tool error listing `worst_flattening_leak_valid`, `ca_centered`, `fl_run_ending_in_recovery`.
- `n` outside 1–50 → tool error.
- Multiple devices on date and no `device_id` → tool error listing device IDs.
- Criterion-irrelevant options passed with non-default values → tool error naming the fields.

---

### compare_epochs

Compare breath-feature distributions across up to 6 labelled therapy date ranges.
Use to detect whether a settings change improved or worsened flow-limitation metrics.
Only nights with OK analysis results and leak-valid breaths contribute to each epoch's
distributions.

Call `get_settings_timeline` first to identify meaningful epoch boundaries, then
`compare_epochs` to quantify differences.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| epochs | list[EpochSpec] | Yes | 1–6 epoch specs, each with `label`, `date_start`, `date_end`, and optional `device_id` |
| metrics | list[str] | No | Metrics to compute (default: all four; see valid values below) |

Each `EpochSpec`:
- `label` — human-readable epoch name (appears in response)
- `date_start` — epoch start in YYYY-MM-DD (inclusive)
- `date_end` — epoch end in YYYY-MM-DD (inclusive)
- `device_id` — optional; all epochs must target the same device

**Valid metrics:** `"mid_insp_flattening"`, `"flatness_index"`, `"tidal_volume_ml"`, `"ie_ratio"`

**Returns:** `CompareEpochsResponse`
- `epochs` — list of `EpochStats`, one per input epoch:
  - `label`, `date_start`, `date_end` — echoed spec
  - `nights_with_data` — nights that had at least one OK-analysis session
  - `nights_missing_analysis` — nights in range lacking OK analysis (skipped)
  - `algorithm_identity` — shared algorithm identity across contributing sessions
  - `null_reason` — present when distributions are null
  - `primary_mode` — uniform primary mode (when applicable)
  - `mid_insp_flattening`, `flatness_index`, `tidal_volume_ml`, `ie_ratio` — `EpochDistribution` with `median`, `iqr`, `p95`, `n_breaths`, `n_nights`
  - `flow_class_distribution` — count of breaths per flow class (`{"0": n, "1": n, ...}`)
  - `rera_proxy_count` — RERA-proxy breath count; null + `rera_reason` when unavailable
  - `rx_settings` — representative therapy settings for this epoch
- `null_reason` — present when ALL epoch distributions are null (cross-epoch refusal)
- `rx_violations` — list of `EpochRxViolationRow`: therapy-settings changes detected within an epoch (use to split the range at `change_dates`)

**Refusal semantics:**
- `null_reason: "algo_version_mismatch"` (cross-epoch, ALL distributions null) — epochs
  span sessions with incompatible algorithm versions; re-run analysis with a uniform
  version.
- `null_reason: "rx_changed_within_epoch"` (cross-epoch, ALL distributions null) —
  therapy settings changed within at least one epoch; `rx_violations` lists the epoch
  label, `changed_keys`, and `change_dates` so the caller can split the range.
- Per-epoch degradation: `null_reason: "primary_mode_mismatch"` on individual epochs
  nulls only RERA-related fields (`rera_proxy_count`, `rera_reason`); FL distributions
  remain populated.

**Error conditions:**
- `epochs` empty or >6 entries → tool error.
- Multiple device IDs across epoch specs → tool error.
- Device not owned by the active profile → tool error.
- Multiple devices in the date union and no `device_id` → tool error listing device IDs so the caller can re-issue with `device_id`.
