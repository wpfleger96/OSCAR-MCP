"""Algorithm versioning types and version constants for PR-A.

Appendix A §1 / plan step 4 (binding typed contracts).

The stored engine_versions_json shape is the nested AlgoVersions composition:

    {
        "identity": AlgorithmIdentity.model_dump(),   # §1
        "run":      AnalysisRunMetadata.model_dump(),  # §1
    }

Legacy flat rows (format_version / modes at top level) are detected by absence
of the "identity" key and treated as STALE_VERSION — no conversion code because
fresh-DB / reimport is the mandated upgrade path.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator

from snore.analysis.modes.config import AVAILABLE_CONFIGS

# ---------------------------------------------------------------------------
# Versioned algorithm constants
# Each constant is stamped into AlgorithmIdentity; bumping any value makes
# prior rows stale and prevents cross-version aggregation.
# ---------------------------------------------------------------------------

SEGMENTER_ALGO_VERSION: str = "v1"
FL_CLASSIFIER_ALGO_VERSION: str = "v1"
FLATTENING_ALGO_VERSION: str = "v1"  # mid-insp flattening (new)
TRIGGER_CYCLE_ALGO_VERSION: str = "v1"  # trigger/cycle heuristic (new, experimental)
LEAK_VALID_ALGO: str = "v1"
RECOVERY_DETECTOR_ALGO_VERSION: str = "v1"

# Flow-derived MV fallback used by get_ca_analysis when no device MV channel
# exists. NOT part of AlgorithmIdentity — it labels query-time derivation only.
MV_FALLBACK_ALGO_VERSION: str = "v1"

# Query-time RERA-proxy criterion (FL runs ending in recovery). NOT part of
# AlgorithmIdentity — it labels query-time derivation only. v2 adds the
# self-contained recovery criterion (class drop to <=2 + peak-flow margin over
# the run mean) alongside the analysis-time recovery flag.
RERA_PROXY_ALGO_VERSION: str = "v2"

# Threshold used by leak_valid derivation (v1).
LEAK_VALID_THRESHOLD_LPM: float = 24.0

# Maximum gap (seconds) for nearest-neighbour leak alignment.
LEAK_VALID_MAX_ALIGNMENT_GAP_S: float = 5.0


# ---------------------------------------------------------------------------
# AlgorithmIdentity — the stable "what algorithm" fingerprint
# ---------------------------------------------------------------------------


class AlgorithmIdentity(BaseModel):
    """Stable algorithm fingerprint persisted with each analysis run.

    Compares structurally.  All query-driving features are included so that
    bumping any version string marks old rows as STALE_VERSION instead of
    silently accepting them.

    Note: run-specific metadata (primary_mode, modes) lives in
    AnalysisRunMetadata — not here.
    """

    format_version: int = 3  # PR-A nested format; 2 = flat legacy rows
    segmenter: str = SEGMENTER_ALGO_VERSION
    fl_classifier: str = FL_CLASSIFIER_ALGO_VERSION
    flattening: str = FLATTENING_ALGO_VERSION
    trigger_cycle: str = TRIGGER_CYCLE_ALGO_VERSION
    leak_valid: str = LEAK_VALID_ALGO
    recovery_detector: str = RECOVERY_DETECTOR_ALGO_VERSION

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlgorithmIdentity):
            return NotImplemented
        return self.model_dump() == other.model_dump()

    @classmethod
    def current(cls) -> AlgorithmIdentity:
        """Return the identity for the current algorithm versions."""
        return cls()


# Fields whose mismatch blocks cross-epoch comparisons (§1 note 3).
CROSS_VERSION_REFUSAL_KEYS: frozenset[str] = frozenset(
    {
        "format_version",
        "segmenter",
        "fl_classifier",
        "flattening",
        "leak_valid",
        "recovery_detector",
    }
)


# ---------------------------------------------------------------------------
# AnalysisRunMetadata — per-invocation context
# ---------------------------------------------------------------------------


class AnalysisRunMetadata(BaseModel):
    """Per-invocation metadata recorded alongside AlgorithmIdentity.

    primary_mode: the single mode whose recovery markers are persisted.
    modes: all modes that were run in this invocation.
    """

    primary_mode: str
    modes: list[str]

    @model_validator(mode="after")
    def _validate(self) -> AnalysisRunMetadata:
        if self.primary_mode not in AVAILABLE_CONFIGS:
            raise ValueError(
                f"primary_mode {self.primary_mode!r} is not a recognised mode. "
                f"Available: {list(AVAILABLE_CONFIGS)}"
            )
        if self.primary_mode not in self.modes:
            raise ValueError(
                f"primary_mode {self.primary_mode!r} must be a member of modes {self.modes}"
            )
        return self


# ---------------------------------------------------------------------------
# AlgoVersions — the composite stored in engine_versions_json
# ---------------------------------------------------------------------------


class AlgoVersions(BaseModel):
    """The composite stored in AnalysisResult.engine_versions_json (PR-A format).

    Validates that the stored JSON contains both the 'identity' and 'run' keys.
    Legacy flat rows (no 'identity' key) do NOT parse here — callers detect them
    by attempting model_validate and catching ValidationError.
    """

    identity: AlgorithmIdentity
    run: AnalysisRunMetadata

    @classmethod
    def from_stored(cls, raw: dict[str, Any]) -> AlgoVersions | None:
        """Parse stored engine_versions_json.  Returns None for legacy flat rows."""
        if "identity" not in raw:
            return None  # legacy flat row → STALE_VERSION
        return cls.model_validate(raw)


# ---------------------------------------------------------------------------
# AnalysisStatus / DayAnalysisStatus / NullReason (§1, §10)
# ---------------------------------------------------------------------------


class AnalysisStatus(StrEnum):
    """Single-session analysis status."""

    OK = "ok"
    NOT_RUN = "not_run"
    STALE_VERSION = "stale_version"


class DayAnalysisStatus(StrEnum):
    """Day-level aggregated analysis status (§10 coverage policy)."""

    OK = "ok"
    PARTIAL = "partial"
    NOT_RUN = "not_run"
    STALE = "stale"
    MIXED_VERSION = "mixed_version"


class NullReason(StrEnum):
    """Codes explaining why an optional field is null."""

    ANALYSIS_NOT_RUN = "analysis_not_run"
    ANALYSIS_STALE = "analysis_stale"
    ALGO_VERSION_MISMATCH = "algo_version_mismatch"
    PRIMARY_MODE_MISMATCH = "primary_mode_mismatch"
    CHANNEL_ABSENT = "channel_absent"
    CHANNEL_UNALIGNED = "channel_unaligned"
    NOT_AVAILABLE = "not_available"
    DURATION_ZERO = "duration_zero"
    NO_DATA_IN_RANGE = "no_data_in_range"
    MULTI_SESSION_AMBIGUITY = "multi_session_ambiguity"
    UNVALIDATED_DEVICE = "unvalidated_device"
    TABLE_MISSING = "table_missing"
    RX_CHANGED_WITHIN_EPOCH = "rx_changed_within_epoch"


class TimezoneStatus(StrEnum):
    """Timestamp timezone classification (A6)."""

    UTC = "utc"
    UNKNOWN = "unknown"
    # Profile-level user-declared IANA timezone applies (timestamps stay naive;
    # the companion timezone_name field carries the declared zone).
    USER_DECLARED = "user_declared"
