# Apple Health Integration

Import Apple Watch sleep and respiratory data into SNORE for night-by-night correlation with your CPAP therapy.

---

## Overview

SNORE can ingest the following data types from Apple Health:

- **Sleep stages** — Core, Deep, REM, Awake, and InBed intervals recorded by Apple Watch
- **Blood oxygen (SpO2)** — overnight spot-checks and continuous readings
- **Respiratory rate** — breaths per minute sampled during sleep
- **Breathing disturbances** — Apple's metric from the watchOS 11 sleep-apnea detection feature

Import your Apple Health export archive using the `snore health import` command. Automatic ongoing sync is planned for a future SNORE iOS app; v1 covers manual `export.zip` imports only.

---

## Historical Backfill

### Export from iPhone

1. Open the **Health** app on your iPhone.
2. Tap your **profile picture** (top-right corner).
3. Scroll to the bottom and tap **Export All Health Data**.
4. Confirm the export — it may take a minute for large libraries.
5. Share the resulting `export.zip` to your computer via AirDrop, a USB cable, or any file transfer method you prefer.

> Exports of multi-year Health libraries can be large — often several hundred MB uncompressed. The import streams records as it reads, but it must scan the entire archive even when using `--from`/`--to` date filters.

### Run the import

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

List recent nights to confirm records are arriving:

```bash
snore health list
```

Output columns: Date | Sleep (hours) | Efficiency % | Core | Deep | REM | Source.

Inspect a specific night in detail (stage intervals, per-stage totals, and quantity samples):

```bash
snore health show 2024-11-15
```

---

## How SNORE Interprets the Data

**Date assignment (noon split).** A sleep session that starts before noon is attributed to the *previous* calendar date — the same noon-to-noon convention used for CPAP days. A session starting at 1 AM on November 15 belongs to the November 14 night. This keeps Apple Watch nights aligned with CPAP therapy days for future correlation.

**Multiple sources.** When both iPhone and Apple Watch report data for the same night, summaries prefer the Watch source and record which source was used.

**Total sleep.** Computed as Core + Deep + REM + Unspecified sleep time. InBed and Awake intervals are tracked separately and excluded from the sleep total.

**Sleep efficiency.** Total sleep divided by time in bed.

**Pre-watchOS 9 data.** Apple Watch did not report individual sleep stages before watchOS 9. Older records import as unspecified sleep with no stage breakdown.

**Limitation — post-import edits.** If you edit or delete a record in Apple Health after it has been imported, re-running the import does not update it. The dedup logic treats each record as an independent row keyed on its original Health UUID; edits in Health produce new UUIDs only if you manually delete and re-enter the record.
