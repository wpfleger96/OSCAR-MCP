# FL/RERA Validation

This document is the canonical record of SNORE's flow-limitation (FL) and RERA validation program: what the metrics are, how they are validated, what validation has found so far, and which decisions were made and why. It is written for the maintainer and for future coding agents picking the work back up. When a new finding lands, append a dated entry to the decision log rather than rewriting history above it.

## Status

SNORE's flow-limitation and RERA metrics are **experimental relative trend instruments, not validated absolute measurements**. They are not validated against device-scored events, and they are not clinically validated. Device-scored events remain the reference standard; the FL/RERA metrics are useful for tracking relative change (for example, night-over-night or across pressure epochs), not for asserting an absolute event count or a clinical severity.

This framing is not aspirational hedging — it is the honest current state, and it must survive edits. The shared disclaimer constant `FL_RERA_EXPERIMENTAL_DISCLAIMER` (`src/snore/constants.py`) carries the canonical wording and is threaded through the MCP tool descriptions and the `docs://schemas` field descriptions. The `/validation` UI view, the day-view glossary, and the API `DayDetail` field descriptions carry equivalent labeling. Any surface that reports these metrics should reach for that constant rather than restating the disclaimer inline.

## The metrics

- **`fl_class_ge4_pct`** — the percent of leak-valid, rule-matched-classified breaths whose `flow_class >= 4`. The rule-matched qualifier is load-bearing: only breaths classified by a shape rule (confidence strictly above `FL_DEFAULT_CONFIDENCE`) are counted. Fallback flatness-triage breaths are excluded. See the fallback mechanism below.
- **`rera_index` / `rera_proxy_count`** — a query-time FL-run proxy for RERA. It counts runs of two or more consecutive breaths with `flow_class >= 4` that end in a recovery breath. The run-detection logic is `iter_fl_run_recoveries` in `src/snore/services/breath/algorithms.py`, versioned as `RERA_PROXY_ALGO_VERSION`. This proxy is distinct from the analysis-time amplitude-crescendo RERA detector; they are separate definitions with separate version constants, and the proxy is what the validation work below scores.

## Validation platform

The validation platform was built in August 2026 across seven merged PRs:

- #296 — Apple Health cross-validator
- #297 — day-view FL/RERA UI
- #298 — RERA range validator
- #299 — day-detail FL/RERA API fields
- #300 — validation-runs persistence
- #301 — offline threshold-sweep harness
- #302 — unified validation UI

It exposes five CLI validators: `validate-fl`, `validate-rera`, `validate-apple`, `validate-breaths`, and `sweep-thresholds`. Persisted validation runs deduplicate on engine identity (`AlgorithmIdentity`) plus validator params, so before-and-after algorithm versions sit side by side in the `/validation` view rather than overwriting one another. This is what makes a version-bumped algorithm experiment legible: run the old and new engines, and the platform keeps both.

## Findings

### Baseline RERA validation

`validate-rera`, run in August 2026 over a 631-session dataset (~56 sessions carried machine-scored RE events), found the RERA proxy to be weakly above chance:

- Pooled precision ≈ 5.7e-4 against a scored-sessions chance-precision floor ≈ 3.6e-4 — roughly 1.6x chance.
- Sensitivity ≈ 5% of machine-flagged RE events.
- An amplitude-variant definition scored ≈ 0%.

The proxy finds a small, weakly-above-chance fraction of machine RE events. It is not a substitute for scored events; it is a relative-trend signal.

### The FL classifier fallback and the 10x reporting discrepancy

The FL classifier (`src/snore/analysis/shared/flow_limitation.py`) triages any breath matching no shape rule on flatness alone: `< 0.5 → class 1`, `0.5–0.7 → class 4`, `>= 0.7 → class 7` (the thresholds are now the named constants `FL_FALLBACK_FLATNESS_CLASS1_MAX` and `FL_FALLBACK_FLATNESS_CLASS4_MAX`). These fallback breaths are stamped at confidence exactly `FL_DEFAULT_CONFIDENCE` (0.5). Rule-matched confidence is always strictly above `FL_DEFAULT_CONFIDENCE`, so the confidence value alone separates a rule-matched classification from a fallback guess.

This separation surfaced a reporting discrepancy diagnosed 2026-08-23. The trigger was an agent session exercising the MCP tools against real data as a normal consumer would: it reported that `compare_epochs`' `flow_class_distribution` showed ≈ 19–27% class≥4 while `get_nightly_summary`'s `fl_class_ge4_pct` showed ≈ 1.4–2.9% for the same nights — an order-of-magnitude gap between two tools that ostensibly measure the same thing. The cause: nightly `fl_class_ge4_pct` counts only rule-matched breaths (`flow_confidence > FL_DEFAULT_CONFIDENCE`), but `compare_epochs` originally counted every classified breath — fallback guesses included.

A direct DB query over one PS 5.0 epoch (2026-08-09 through 2026-08-19, leak-valid breaths) confirmed the mechanism. Rule-matched class≥4 breaths were 1,702 of 63,360 = 2.7%, matching the nightly figure. The fallback bucket held 38,807 class-1 guesses, 25,924 class-4 guesses, and 63 class-7 guesses — and those 25,924 fallback class-4 guesses were the entire gap. Neither pipeline was broken; they disagreed on what "class≥4" meant. The dose-response signal (PS 4.4 FL ≈ 1.4–1.5x PS 5.0) survives under both definitions.

The same MCP smoke-testing rounds surfaced a second reporting bug: a three-epoch `compare_epochs` call where only the middle epoch contained a settings change (an EPAP change on 2026-08-08) came back with all three epochs nulled under `rx_changed_within_epoch`, not just the violating one. A single mid-range Rx change was voiding the whole comparison rather than the one epoch it fell in.

Resolution landed in #306/#307 (merged 2026-08-23, squashed as one commit): `compare_epochs`' `flow_class_distribution` now applies the nightly confidence gate and reconciles with `fl_class_ge4_pct`; the fallback guesses are reported separately in `flow_class_distribution_fallback` rather than being silently folded in or dropped; a mid-epoch Rx change now nulls only the violating epoch instead of voiding the whole comparison; and experimental labeling was threaded through the MCP surfaces. The standing constraint was honored throughout — reporting-layer changes only, no analysis-algorithm changes.

**How these were found — a lesson worth keeping.** Both bugs came from exercising the MCP surface with an agent as a real consumer, not from the unit suite. Per-tool unit tests passed because each tool was internally self-consistent; the failures were cross-tool inconsistencies — two tools silently disagreeing on a definition, and one tool over-nulling — that only show up when something drives the tools together against real data and cross-checks their answers. Both turned out to be reporting-layer definition mismatches, not algorithm regressions. Driving the MCP tools end to end as a consumer is worth doing precisely because it catches the class of bug that isolated unit tests structurally cannot.

### Apple Health cross-validation

`validate-apple`, run in August 2026 over a 645-session dev DB (full range 2025-02-21 through 2026-08-22, 504 paired nights), compared the nightly metrics against Apple Health's breathing-disturbance (BD) signal:

- `rera_index` vs Apple BD: ρ = −0.09 (p = 0.041).
- `fl_class_ge4_pct` vs Apple BD: ρ = −0.19 (p = 1.5e-5).

Both full-range correlations are negative, but a per-epoch analysis across the twelve constant-settings epochs told two different stories:

- **RERA vs Apple BD is confounding.** The sign flips epoch to epoch, nothing is significant, and the biggest epochs lean positive. The full-range negative correlation is epoch/titration confounding — confirmed.
- **FL vs Apple BD is a genuine within-epoch inverse relation.** Nine of twelve epochs are negative, two are nominally significant (ρ = −0.44 at n = 24; ρ = −0.72 at n = 11), and two are near significance (ρ = −0.50 at n = 15; ρ = −0.18 at n = 111). This survives the per-epoch decomposition, so it is not confounding — it is a real inverse relation that needs an explanation. See open questions.

## Decision log

### 2026-08 — validation platform built (#296–#302)

Seven PRs delivered the Apple Health cross-validator, day-view FL/RERA UI, RERA range validator, day-detail API fields, validation-runs persistence, offline threshold-sweep harness, and unified validation UI, plus the five CLI validators. This is the machinery every finding below runs on.

### 2026-08-23 — reporting reconciliation (#306/#307)

Both fixed bugs were surfaced by MCP smoke-testing rounds (an agent driving the tools against real data), not by the unit suite. Diagnosed the 10x FL reporting discrepancy as a definitional disagreement between the nightly confidence gate and `compare_epochs`, not a bug in either pipeline. Reconciled `compare_epochs` onto the nightly gate, split fallback guesses into `flow_class_distribution_fallback`, and fixed the batch-null bug so a mid-epoch Rx change nulls only the violating epoch instead of all epochs in the comparison. Reporting-layer only; no analysis-algorithm change.

### 2026-08-23 — fallback-exclusion probe (#309) → keep the fallback

The RERA proxy's FL-run scan intentionally includes fallback flatness-triage guesses. The open question was whether that is noise or signal. Two candidate production changes were on the table, to be run behind a version bump through the validation platform: variant A (unmatched breaths become unclassified) and variant B (keep the fallback but gate it out of FL-run detection). Rather than pay for a full version-bump experiment up front, a cheap offline probe went first, via a new `include_fallback` knob in `snore sweep-thresholds` (#309). Setting it to `0.0` masks fallback breaths' `flow_class` to null at sweep load time; the knob is sweep-harness-only and leaves the production seams untouched.

The probe ran on the dev DB (2025-02-21 through 2026-08-22) with the other knobs at production defaults (`fl_class_threshold=4`, `min_fl_run_length=2`, `recovery_amplitude_margin=0.20`):

- **`re` target** (530 sessions loaded, 55 machine RE events, 5s match tolerance; this sweep's pooled chance floor is 3.79e-5 — computed over all loaded sessions, hence lower than `validate-rera`'s scored-sessions floor). Fallback included: 41,876 proxy events (10.4/h), sensitivity 3.6% (2/55), precision 3.91e-4 (~10x this floor). Fallback excluded: 782 events (0.19/h, −98%), sensitivity 0, precision 0.
- **Decision rule, agreed in advance:** if precision lifted above chance when the fallback was gated out, gate it out; if sensitivity collapsed with no precision gain, the fallback earns its keep. The outcome was a total collapse of both precision and sensitivity, so **the fallback is load-bearing: keep it, and run no version-bump experiment.** Rule-matched class≥4 breaths are simply too sparse to form runs of two or more consecutive breaths. Variant A is rejected for the same reason — it would additionally perturb the session FL index for the same null result.
- **`apple` target side finding** (504 paired nights, full range): the Spearman ρ of the nightly proxy index vs Apple Health breathing disturbance moved from −0.094 (p = 0.035) with the fallback to −0.128 (p = 0.004) without it. The inverse FL-vs-Apple relation is not fallback noise — the rule-matched-only signal is *more* inversely related. Caveat: full-range correlations are epoch-confounded, so this is a pointer, not a conclusion.

## Open questions

Why does SNORE FL inversely track Apple breathing disturbances *within* constant-settings epochs? The per-epoch FL-vs-Apple relation is genuine (not confounding), yet inverse, and the fallback-exclusion probe showed the rule-matched-only FL signal is more inversely related, not less. The leading interpretation is that SNORE FL may not measure the same construct as Apple BD, but that is open. The concrete next step when this is picked back up is a per-epoch re-run of the Apple cross-validation using the rule-matched-only (fallback-excluded) FL signal.
