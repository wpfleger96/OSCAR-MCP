"""Validator registry — the single seam for wiring a validator into persistence.

Each ``validator_type`` maps to a :class:`ValidatorSpec` bundling three things
the persistence layer needs and nothing else:

- ``run``            — how to construct and invoke the validator, returning its
                       Pydantic report.
- ``current_params`` — the query-time knobs in effect *right now* that
                       ``AlgorithmIdentity`` does NOT fingerprint (mode, FLG
                       thresholds, RERA-proxy tunables, event-match tolerance).
                       This is the params half of the run dedup/comparison key;
                       without it, a threshold change would silently compare
                       unlike runs as equal.
- ``mode``           — JOB (queued background run) or SYNC (computed inline in
                       the POST, row written straight to SUCCEEDED).

Registering a new validator (e.g. ``rera``, ``apple``) is ONE additive change:
import its ``run``/``current_params`` and add a single ``register(...)`` call at
the bottom of this module.  Nothing else in the persistence/jobs/API layer needs
to change — unregistered types in ``VALIDATOR_TYPES`` are accepted by the request
schema but rejected with a clear 400 until their spec is registered here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession

# Request-accepted validator types.  ``events``/``fl``/``breaths`` are registered
# below; ``rera``/``apple`` are accepted here so the request schema and DB CHECK
# admit them, but stay UNREGISTERED (their sibling validators land separately) —
# a run request for them returns a clear 400 until ``register(...)`` is added.
VALIDATOR_TYPES: tuple[str, ...] = ("events", "fl", "breaths", "rera", "apple")


class RunMode(StrEnum):
    """How a validator's run is executed and persisted."""

    JOB = "job"  # Background job: queued, minutes-long FLG deserialization.
    SYNC = "sync"  # Computed inline in the POST; row written state=succeeded.


# (db, profile_id, date_from_iso, date_to_iso, params) -> report model
RunFn = Callable[
    ["AsyncSession", int, str, str, dict[str, Any]], "Awaitable[BaseModel]"
]
# (request_params | None) -> the canonical params dict for the dedup key
ParamsFn = Callable[[dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class ValidatorSpec:
    validator_type: str
    mode: RunMode
    run: RunFn
    current_params: ParamsFn


_REGISTRY: dict[str, ValidatorSpec] = {}


def register(spec: ValidatorSpec) -> None:
    """Register (or replace) the spec for ``spec.validator_type``."""
    _REGISTRY[spec.validator_type] = spec


def get_spec(validator_type: str) -> ValidatorSpec | None:
    """Return the registered spec, or None if the type is not yet wired."""
    return _REGISTRY.get(validator_type)


def registered_types() -> frozenset[str]:
    return frozenset(_REGISTRY)


def engine_identity() -> dict[str, Any]:
    """The current algorithm identity — the engine half of the dedup key.

    Shared by every validator type: it fingerprints the analysis engine the
    validators run over, so bumping any algorithm version forces fresh runs.
    """
    from snore.analysis.shared.versioning import AlgorithmIdentity  # noqa: PLC0415

    return AlgorithmIdentity.current().model_dump()


# ---------------------------------------------------------------------------
# events — apnea/hypopnea event-match validation (BatchValidator)
# ---------------------------------------------------------------------------


async def _run_events(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    params: dict[str, Any],
) -> BaseModel:
    from snore.validation import BatchValidator  # noqa: PLC0415

    return await BatchValidator(db, profile_id).validate_date_range(
        date_from=date_from, date_to=date_to, mode=str(params.get("mode", "aasm"))
    )


def _params_events(request_params: dict[str, Any] | None) -> dict[str, Any]:
    # The detection mode is the query-time knob AlgorithmIdentity does not carry;
    # it selects which mode_result the event match compares against.
    return {"mode": str((request_params or {}).get("mode", "aasm"))}


# ---------------------------------------------------------------------------
# fl — flow-limitation signal validation (FlowLimitationValidator)
# ---------------------------------------------------------------------------


async def _run_fl(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    params: dict[str, Any],
) -> BaseModel:
    from snore.validation import FlowLimitationValidator  # noqa: PLC0415

    return await FlowLimitationValidator(db, profile_id).validate_date_range(
        date_from=date_from, date_to=date_to
    )


def _params_fl(request_params: dict[str, Any] | None) -> dict[str, Any]:
    from snore.constants import FlowLimitationConstants as FLC  # noqa: PLC0415

    # The query-time threshold the FL comparator reads that AlgorithmIdentity's
    # fl_classifier version string does NOT pin: the default-confidence floor
    # gating which breaths count as rule-matched.  A change here must force a
    # fresh run, so it belongs in the params half of the dedup key.
    return {"fl_default_confidence": FLC.FL_DEFAULT_CONFIDENCE}


# ---------------------------------------------------------------------------
# breaths — breath-trends cross-validation (BreathTrendsValidator)
# ---------------------------------------------------------------------------


async def _run_breaths(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    params: dict[str, Any],
) -> BaseModel:
    from snore.validation import BreathTrendsValidator  # noqa: PLC0415

    return await BreathTrendsValidator(db, profile_id).validate_date_range(
        date_from=date_from, date_to=date_to
    )


def _params_breaths(request_params: dict[str, Any] | None) -> dict[str, Any]:
    # Pure signal alignment against device trend channels — no query-time tunable
    # beyond AlgorithmIdentity.  Empty by design; the params component still
    # participates in the dedup key (an empty dict compares equal across runs).
    return {}


# ---------------------------------------------------------------------------
# rera — RERA-proxy vs. device RERA validation (ReraValidator)
# ---------------------------------------------------------------------------


async def _run_rera(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    params: dict[str, Any],
) -> BaseModel:
    from snore.validation import ReraValidator  # noqa: PLC0415

    return await ReraValidator(db, profile_id).validate_date_range(
        date_from=date_from, date_to=date_to
    )


def _params_rera(request_params: dict[str, Any] | None) -> dict[str, Any]:
    from snore.analysis.modes.postprocess import (  # noqa: PLC0415
        EVENT_MATCH_TOLERANCE_SECONDS,
    )
    from snore.analysis.shared.versioning import (  # noqa: PLC0415
        RERA_PROXY_ALGO_VERSION,
    )
    from snore.constants import RERAProxyConstants  # noqa: PLC0415

    # The query-time RERA-proxy tunables (breath_service.py) plus the event-match
    # tolerance — none of these are fingerprinted by AlgorithmIdentity, so a
    # change to any must force a fresh run.  RERA_PROXY_ALGO_VERSION is versioned
    # separately from the analysis-time detector.
    return {
        "rera_proxy_algo_version": RERA_PROXY_ALGO_VERSION,
        "fl_class_threshold": RERAProxyConstants.FL_CLASS_THRESHOLD,
        "min_fl_run_length": RERAProxyConstants.MIN_FL_RUN_LENGTH,
        "recovery_amplitude_margin": RERAProxyConstants.RECOVERY_AMPLITUDE_MARGIN,
        "match_tolerance_seconds": EVENT_MATCH_TOLERANCE_SECONDS,
    }


# ---------------------------------------------------------------------------
# apple — night-level cross-validation vs. Apple Health (AppleCrossValidator)
# ---------------------------------------------------------------------------


async def _run_apple(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    params: dict[str, Any],
) -> BaseModel:
    from snore.validation import AppleCrossValidator  # noqa: PLC0415

    device_id = params.get("device_id")
    return await AppleCrossValidator(db, profile_id).validate_date_range(
        date_from=date_from,
        date_to=date_to,
        device_id=device_id if device_id is None else int(device_id),
    )


def _params_apple(request_params: dict[str, Any] | None) -> dict[str, Any]:
    from snore.validation.apple_cross_report import _MIN_PAIRS  # noqa: PLC0415

    # Night-level correlation over already-computed nightly summaries; the only
    # query-time knob is the minimum paired-night count below which the Spearman
    # correlation is reported as insufficient rather than computed.
    params: dict[str, Any] = {"min_pairs": _MIN_PAIRS}
    # Pinning the SNORE device changes which nights are resolved (and which
    # degrade to device_ambiguous), so a pinned run must not dedup onto an
    # unpinned one.  Include device_id only when supplied — omitting it keeps
    # the key identical to the pre-pinning behavior for the common case.
    device_id = (request_params or {}).get("device_id")
    if device_id is not None:
        params["device_id"] = int(device_id)
    return params


register(
    ValidatorSpec(
        validator_type="events",
        mode=RunMode.JOB,
        run=_run_events,
        current_params=_params_events,
    )
)
register(
    ValidatorSpec(
        validator_type="fl",
        mode=RunMode.JOB,
        run=_run_fl,
        current_params=_params_fl,
    )
)
register(
    ValidatorSpec(
        validator_type="breaths",
        mode=RunMode.JOB,
        run=_run_breaths,
        current_params=_params_breaths,
    )
)
register(
    ValidatorSpec(
        validator_type="rera",
        mode=RunMode.JOB,
        run=_run_rera,
        current_params=_params_rera,
    )
)
register(
    ValidatorSpec(
        validator_type="apple",
        mode=RunMode.SYNC,
        run=_run_apple,
        current_params=_params_apple,
    )
)
