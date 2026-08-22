"""Single source of truth for the "hours" denominator of AHI-family indices.

AHI, OAI, CAI, HI, RDI and related rates are all ``events / hours``.  Before
issue #275 four incompatible denominators had grown up independently across the
parsers, the analysis service and the CLI display layer (mask-on time, session
span, waveform sample coverage, and ad-hoc timestamp deltas), so the same night
could yield different indices depending on which code path computed them.

This module defines the denominator once.  Each call selects exactly one basis
and never silently falls back to another: a caller that needs a fallback must
request the next basis itself and decide what an absent input means.  This keeps
the "unknown" case (input missing) distinct from the "known-zero" case (input
present but empty), which the AHI computation must treat differently.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

_SECONDS_PER_HOUR = 3600.0


class TherapyHoursBasis(StrEnum):
    """Which measurement the therapy-hours denominator is derived from."""

    MASK_ON = "mask_on"
    SESSION_SPAN = "session_span"
    WAVEFORM_COVERAGE = "waveform_coverage"


def therapy_hours(
    basis: TherapyHoursBasis,
    *,
    mask_on_segments: Sequence[tuple[float, float]] | None = None,
    span_seconds: float | None = None,
    sample_count: int | None = None,
    sample_rate: float | None = None,
) -> float | None:
    """Compute the therapy-hours denominator for a single basis.

    Exactly one basis is evaluated per call; there is no hidden fallback
    between bases.  ``None`` means the requested basis's input is absent
    ("unknown"), which callers must distinguish from a returned ``0.0``
    ("known-zero") — in particular an empty ``mask_on_segments`` list is
    known-zero and must not be replaced by the session span.

    Args:
        basis: Which measurement to derive the hours from.
        mask_on_segments: ``(start, end)`` pairs in seconds of mask-on wear,
            for ``MASK_ON``.  ``None`` is unknown; ``[]`` is known-zero.
        span_seconds: Session span in seconds, for ``SESSION_SPAN``.
        sample_count: Number of waveform samples, for ``WAVEFORM_COVERAGE``.
        sample_rate: Sample rate in Hz, for ``WAVEFORM_COVERAGE``.

    Returns:
        The denominator in hours, or ``None`` when the requested basis's
        inputs are absent (or ``sample_rate <= 0`` for ``WAVEFORM_COVERAGE``).
    """
    if basis is TherapyHoursBasis.MASK_ON:
        if mask_on_segments is None:
            return None
        return sum(end - start for start, end in mask_on_segments) / _SECONDS_PER_HOUR

    if basis is TherapyHoursBasis.SESSION_SPAN:
        if span_seconds is None:
            return None
        return span_seconds / _SECONDS_PER_HOUR

    if sample_count is None or sample_rate is None or sample_rate <= 0:
        return None
    return sample_count / sample_rate / _SECONDS_PER_HOUR
