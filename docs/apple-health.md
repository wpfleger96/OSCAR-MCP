# Apple Health Integration

Import Apple Watch sleep and respiratory data into SNORE for night-by-night correlation with your CPAP therapy.

---

## Overview

SNORE can ingest the following data types from Apple Health:

- **Sleep stages** — Core, Deep, REM, Awake, and InBed intervals recorded by Apple Watch
- **Blood oxygen (SpO2)** — overnight spot-checks and continuous readings
- **Respiratory rate** — breaths per minute sampled during sleep
- **Breathing disturbances** — Apple's metric from the watchOS 11 sleep-apnea detection feature

You can import your Apple Health export archive through the SNORE web UI or the `snore health import` CLI command. Automatic ongoing sync is planned for a future SNORE iOS app; v1 covers manual `export.zip` imports only.

---

## Historical Backfill

### Export from iPhone

1. Open the **Health** app on your iPhone.
2. Tap your **profile picture** (top-right corner).
3. Scroll to the bottom and tap **Export All Health Data**.
4. Confirm the export — it may take a minute for large libraries.
5. Share the resulting `export.zip` to your computer via AirDrop, a USB cable, or any file transfer method you prefer.

> Exports of multi-year Health libraries can be large — often several hundred MB uncompressed. The import streams records as it reads, but it must scan the entire archive even when using `--from`/`--to` date filters.

### Import via the web UI

With SNORE running (`snore serve`), open the Import page and select the **Apple Health** tab. Click the file picker zone, choose your `export.zip`, then click **Import**. A progress bar tracks the upload; once it completes the job appears in the jobs panel below the upload card, showing a summary line such as "12345 samples · 42 nights · 0 duplicates" once the server finishes processing. Re-imports are safe — records that were already imported are counted as duplicates and skipped.

### Import via the CLI

```bash
snore health import export.zip
```

SNORE accepts the zip directly; no extraction needed. If you have already unzipped it, pass the directory path instead:

```bash
snore health import ~/Downloads/apple_health_export/
```

**Useful flags:**

| Flag | Effect |
|------|--------|
| `--dry-run` | Preview what would be imported without writing to the database |
| `--from DATE` | Skip records before this date (ISO 8601, e.g. `2024-01-01`) |
| `--to DATE` | Skip records after this date |
| `--limit N` | Stop after importing N records (useful for testing) |
| `--batch-size N` | Control database batch size (default is reasonable for most machines) |

**Re-imports are safe.** Ingestion is idempotent — running the same import twice reports 0 new records rather than creating duplicates.

---

## Verifying Data Arrival

### CLI

List recent nights to confirm records are arriving:

```bash
snore health list
```

Output columns: Date | Sleep (hours) | Efficiency % | Core | Deep | REM | Source.

Inspect a specific night in detail (stage intervals, per-stage totals, and quantity samples):

```bash
snore health show 2024-11-15
```

### Web UI

After importing, open **Apple Health** in the sidebar. The list page shows every imported night with per-night sleep stats: Total Sleep, Efficiency, Core, Deep, REM, and the source device. Average stat cards at the top of the list summarise the visible page.

---

## The Apple Health Section

The **Apple Health** sidebar entry leads to a paginated list of nights. Clicking any row opens the night detail page at `/apple-health/{date}`, which shows:

- Stat cards for Time in Bed, Total Sleep, Efficiency, Core, Deep, REM, Awake, and Stage Coverage.
- When available, a second row of cards for SpO₂ average, SpO₂ minimum, and average respiratory rate.
- A **Sleep Stages** hypnogram (timeline chart) when stage-level samples are present. Lanes from bottom to top are: In Bed, Awake, Core (N1+N2), Deep (N3), and REM. Unspecified sleep (pre-watchOS 9 data) shares the Core lane in a muted colour.
- A **View CPAP day** link to `/days/{date}` for the same calendar date.

### Sleep data in CPAP views

Sleep stats surface in several CPAP-keyed views when a matching Apple Health night exists for the same date:

- **Day detail** (`/days/{date}`) — an "Apple Health" section below the CPAP stats shows Time in Bed, Total Sleep, Efficiency, Core, Deep, and REM stat cards, plus an "Apple Health night detail →" link back to `/apple-health/{date}`.
- **Session detail** — a collapsible "Apple Health" section shows Total Sleep, Efficiency, Deep, REM, SpO₂ average (when present), and respiratory rate average (when present), with links to both the Apple Health night detail and the therapy day.
- **Dashboard** — when sleep data is available, average sleep duration and average sleep efficiency appear as stat cards alongside the CPAP summary row.
- **Stats** — sleep trends (Sleep Efficiency over time), records (best Total Sleep), and period averages (Avg Sleep and Sleep Efficiency) are included when sleep data has been imported and the period contains nights with data.

---

## Behavioral Notes

**Date assignment (noon split).** A sleep session that starts before noon is attributed to the *previous* calendar date — the same noon-to-noon convention used for CPAP days. A session starting at 1 AM on November 15 belongs to the November 14 night. This keeps Apple Watch nights aligned with CPAP therapy days for correlation. Apple's Health app sometimes labels a night by the morning wake-up date rather than the evening date — if a night appears to be off by one, check the adjacent date before concluding there is a mismatch.

**Multiple sources.** When both iPhone and Apple Watch report data for the same night, summaries prefer the Watch source and record which source was used.

**Total sleep.** Computed as Core + Deep + REM + Unspecified sleep time. InBed and Awake intervals are tracked separately and excluded from the sleep total.

**Sleep efficiency.** Total sleep divided by time in bed.

**Pre-watchOS 9 data.** Apple Watch did not report individual sleep stages before watchOS 9. Older records import as unspecified sleep with no stage breakdown.

**Import queue.** Apple Health uploads share the same background worker as CPAP imports and are processed serially. A large health archive submitted while a CPAP import is running will queue behind it; both jobs are visible in the jobs panel.

**Nights without CPAP data.** The day detail page at `/days/{date}` is CPAP-keyed — it returns a 404 when no CPAP therapy data exists for that date. A night that contains only Apple Health data lives at `/apple-health/{date}`; the corresponding CPAP day view will not exist.

**Limitation — post-import edits.** If you edit or delete a record in Apple Health after it has been imported, re-running the import does not update it. The dedup logic treats each record as an independent row keyed on its original Health UUID; edits in Health produce new UUIDs only if you manually delete and re-enter the record.
