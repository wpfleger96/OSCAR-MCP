"""Unit tests for the shared numeric helpers in snore.utils.stats."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from snore.utils.stats import (
    percentile_nearest_rank,
    usage_weighted_means,
    weighted_mean,
)

# ---------------------------------------------------------------------------
# percentile_nearest_rank
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_none():
    assert percentile_nearest_rank([], 0.5) is None


def test_percentile_single_value_any_q():
    assert percentile_nearest_rank([7.0], 0.0) == 7.0
    assert percentile_nearest_rank([7.0], 0.5) == 7.0
    assert percentile_nearest_rank([7.0], 1.0) == 7.0


def test_percentile_median_of_even_list():
    # len=4, q=0.5 -> index min(2, 3) = 2
    assert percentile_nearest_rank([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0


def test_percentile_q_one_clamps_to_last():
    # len * 1.0 == len would overrun; index clamps to len - 1
    assert percentile_nearest_rank([1.0, 2.0, 3.0], 1.0) == 3.0


def test_percentile_q_zero_is_first():
    assert percentile_nearest_rank([1.0, 2.0, 3.0], 0.0) == 1.0


def test_percentile_p95_typical():
    vals = [float(i) for i in range(1, 101)]  # 1..100
    # int(100 * 0.95) = 95 -> vals[95] == 96.0
    assert percentile_nearest_rank(vals, 0.95) == 96.0


# ---------------------------------------------------------------------------
# weighted_mean
# ---------------------------------------------------------------------------


def test_weighted_mean_empty_returns_none():
    assert weighted_mean([]) is None


def test_weighted_mean_zero_weights_returns_none():
    assert weighted_mean([(5.0, 0.0), (7.0, 0.0)]) is None


def test_weighted_mean_negative_total_weight_returns_none():
    assert weighted_mean([(5.0, -2.0)]) is None


def test_weighted_mean_single_pair():
    assert weighted_mean([(5.0, 3.0)]) == 5.0


def test_weighted_mean_hand_computed():
    # (5*8 + 7*4) / (8+4) = 68/12
    assert weighted_mean([(5.0, 8.0), (7.0, 4.0)]) == pytest.approx(68 / 12)


# ---------------------------------------------------------------------------
# usage_weighted_means
# ---------------------------------------------------------------------------

_FIELD_MAP = {"epap": "epap_mean", "rr": "respiratory_rate_mean"}


def _hours(row: SimpleNamespace) -> float | None:
    return row.usage_hours


def _row(hours: float | None, epap: float | None, rr: float | None) -> SimpleNamespace:
    return SimpleNamespace(usage_hours=hours, epap_mean=epap, respiratory_rate_mean=rr)


def test_usage_weighted_means_empty_rows():
    assert usage_weighted_means([], _FIELD_MAP, _hours) == {"epap": None, "rr": None}


def test_usage_weighted_means_skips_none_and_nonpositive_hours():
    rows = [
        _row(None, 5.0, 14.0),
        _row(0.0, 5.0, 14.0),
        _row(-1.0, 5.0, 14.0),
    ]
    assert usage_weighted_means(rows, _FIELD_MAP, _hours) == {"epap": None, "rr": None}


def test_usage_weighted_means_single_row():
    rows = [_row(8.0, 5.0, 14.0)]
    assert usage_weighted_means(rows, _FIELD_MAP, _hours) == {"epap": 5.0, "rr": 14.0}


def test_usage_weighted_means_hand_computed():
    rows = [_row(8.0, 5.0, 14.0), _row(4.0, 7.0, 16.0)]
    result = usage_weighted_means(rows, _FIELD_MAP, _hours)
    assert result["epap"] == pytest.approx((5.0 * 8 + 7.0 * 4) / 12)
    assert result["rr"] == pytest.approx((14.0 * 8 + 16.0 * 4) / 12)


def test_usage_weighted_means_missing_values_excluded_per_key():
    # Second row has no epap value: its hours count toward rr only.
    rows = [_row(8.0, 5.0, 14.0), _row(4.0, None, 16.0)]
    result = usage_weighted_means(rows, _FIELD_MAP, _hours)
    assert result["epap"] == pytest.approx(5.0)
    assert result["rr"] == pytest.approx((14.0 * 8 + 16.0 * 4) / 12)


def test_usage_weighted_means_all_values_missing_for_key():
    rows = [_row(8.0, None, 14.0), _row(4.0, None, 16.0)]
    result = usage_weighted_means(rows, _FIELD_MAP, _hours)
    assert result["epap"] is None
    assert result["rr"] == pytest.approx((14.0 * 8 + 16.0 * 4) / 12)
