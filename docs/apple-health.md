# Apple Health Integration

Import Apple Watch sleep and respiratory data into SNORE for night-by-night correlation with your CPAP therapy.

---

## Overview

SNORE can ingest the following data types from Apple Health:

- **Sleep stages** — Core, Deep, REM, Awake, and InBed intervals recorded by Apple Watch
- **Blood oxygen (SpO2)** — overnight spot-checks and continuous readings
- **Respiratory rate** — breaths per minute sampled during sleep
- **Breathing disturbances** — Apple's metric from the watchOS 11 sleep-apnea detection feature

Two pathways are available:

**Historical backfill** exports everything stored in the Health app as a zip archive and imports it in one shot. This is the right starting point for years of existing Watch data.

**Ongoing sync** uses the third-party iOS app [Health Auto Export – JSON+CSV](https://www.healthexportapp.com/) (HAE, by Lybron Sobers) to background-push new data to SNORE's REST endpoint while `snore serve` is running. Apple provides no web or cloud API for Health data — HealthKit access requires a native iOS app running on the device — which is why a third-party bridge app is needed for automation.

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

## Ongoing Sync with Health Auto Export

This pathway keeps SNORE up to date automatically. Once configured, HAE pushes new overnight data to SNORE within a few hours of your Watch syncing.

### Prerequisites

- **Health Auto Export – JSON+CSV** installed from the App Store
- HAE **Premium** unlocked ($24.99 lifetime) — the REST API destination feature requires Premium
- `snore serve` running and reachable from your iPhone (see [Network Reachability](#network-reachability-wsl2-note) below)

### Step 1 — Grant Health access

In the Health app (or when HAE prompts on first launch), grant HAE **read** access to:

- Sleep
- Blood Oxygen
- Respiratory Rate
- Breathing Disturbances (listed under "Respiratory" in watchOS 11+)

### Step 2 — Mint an ingest token

```bash
snore health token create --label "iphone-hae"
```

The plaintext token is printed **once**. Copy it now — only a hash is stored in the database, so it cannot be recovered later. If you lose it, revoke and create a new one.

To review existing tokens:

```bash
snore health token list
```

To revoke a token:

```bash
snore health token revoke <id>
```

### Step 3 — Configure an HAE Automation

In HAE, create a new **Automation** with these settings:

| Setting | Value |
|---------|-------|
| Export format | JSON |
| Data types | Sleep, Blood Oxygen, Respiratory Rate, Breathing Disturbances |
| Sleep aggregation | **Off / Unaggregated** (critical — see note below) |
| Destination | REST API |
| URL | `http://<server>:<port>/api/v1/health/ingest` |
| Custom header name | `X-SNORE-Ingest-Token` |
| Custom header value | `<token from step 2>` |
| Schedule | Your preferred interval (e.g. every morning) |

> **Aggregated mode collapses stage segments to nightly totals and loses all timing data.** SNORE requires unaggregated intervals to reconstruct per-stage durations and align stages with CPAP session timestamps. Always disable aggregation.

### Delivery timing

HAE pushes data in the background; iOS schedules these tasks opportunistically. Expect data to arrive within a few hours of your Watch syncing each morning, not in real time. Duplicate pushes are harmless — the ingest endpoint is idempotent.

---

## Network Reachability (WSL2 Note)

Your iPhone must be able to reach the machine running `snore serve`. On a standard home network this is straightforward. If SNORE runs inside WSL2, extra steps are required because WSL2 has its own internal IP that is not directly accessible from the LAN.

**Option 1 — Windows port proxy (LAN only)**

Run these two commands in an elevated PowerShell prompt, substituting your WSL2 IP (find it with `ip addr show eth0` inside WSL) and the port SNORE listens on:

```powershell
netsh interface portproxy add v4tov4 listenport=<port> listenaddress=0.0.0.0 connectport=<port> connectaddress=<wsl2-ip>
netsh advfirewall firewall add rule name="SNORE ingest" dir=in action=allow protocol=TCP localport=<port>
```

Your iPhone can then reach `http://<windows-lan-ip>:<port>/api/v1/health/ingest`.

Note: the WSL2 IP changes on each WSL restart. If the proxy stops working after a restart, re-run the `portproxy` command with the updated IP.

**Option 2 — Tailscale (simpler, works away from home)**

Install Tailscale on both your iPhone and your machine. Both devices get stable private IPs regardless of network, and no port-proxy setup is needed. Use the Tailscale IP in the HAE destination URL.

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

After an HAE push, confirm the token saw recent activity:

```bash
snore health token list
```

The `last_used_at` timestamp updates on each successful ingest.

---

## How SNORE Interprets the Data

**Date assignment (noon split).** A sleep session that starts before noon is attributed to the *previous* calendar date — the same noon-to-noon convention used for CPAP days. A session starting at 1 AM on November 15 belongs to the November 14 night. This keeps Apple Watch nights aligned with CPAP therapy days for future correlation.

**Multiple sources.** When both iPhone and Apple Watch report data for the same night, summaries prefer the Watch source and record which source was used.

**Total sleep.** Computed as Core + Deep + REM + Unspecified sleep time. InBed and Awake intervals are tracked separately and excluded from the sleep total.

**Sleep efficiency.** Total sleep divided by time in bed.

**Pre-watchOS 9 data.** Apple Watch did not report individual sleep stages before watchOS 9. Older records import as unspecified sleep with no stage breakdown.

**Limitation — post-import edits.** If you edit or delete a record in Apple Health after it has been imported, re-running the import does not update it. The dedup logic treats each record as an independent row keyed on its original Health UUID; edits in Health produce new UUIDs only if you manually delete and re-enter the record.

---

## Troubleshooting

**401 Unauthorized from the ingest endpoint**
The token is missing, wrong, or revoked. Verify the HAE header name is exactly `X-SNORE-Ingest-Token` (case-sensitive) and the value matches what `snore health token create` printed. If the token was lost, revoke it and create a new one.

**No data arriving after HAE pushes**
1. Confirm `snore serve` is running.
2. Test reachability from your phone: open `http://<server>:<port>/api/v1/health/ingest` in Safari — you should get a 405 (Method Not Allowed), not a connection error.
3. If behind WSL2, verify the port proxy is in place and the WSL2 IP hasn't changed since it was set up.
4. Check that the HAE Automation is enabled and the schedule has elapsed.

**Sleep totals look wrong**
Almost always caused by HAE's aggregated mode being left on. In HAE, open the Automation, find the sleep aggregation setting, and switch it to unaggregated. Then trigger a manual export to backfill correctly-formatted records.
