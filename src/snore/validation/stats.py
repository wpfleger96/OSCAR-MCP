"""Shared statistical helpers for validation modules.

Extracted from fl_validator for reuse by fl_validator and breath-trends
validation (and any future signal-level validators).
"""

from __future__ import annotations

import warnings

import numpy as np

from scipy.stats import spearmanr


def spearman_or_none(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    """Compute Spearman r and p-value; return (None, None) on degenerate inputs.

    Returns (None, None) when fewer than 3 samples are provided, or when scipy
    returns NaN (e.g. constant-input edge cases).  Suppresses scipy warnings.
    """
    if len(x) < 3:
        return None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = spearmanr(x, y)
    r = float(result.statistic)
    p = float(result.pvalue)
    if not (np.isfinite(r) and np.isfinite(p)):
        return None, None
    return r, p


def mean_or_none(vals: list[float]) -> float | None:
    """Mean of a list; None for an empty list."""
    return sum(vals) / len(vals) if vals else None
