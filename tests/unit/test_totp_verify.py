"""Unit tests for verify_totp_code in snore.auth.totp.

Tests the pure-function verification logic directly — no HTTP, no database.
All assertions operate on (ok, matched_step) return values from verify_totp_code.
"""

from __future__ import annotations

import pyotp
import pytest

from snore.auth.totp import TOTP_PERIOD, verify_totp_code

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STEP = 50_000  # arbitrary stable reference step well away from 0


def _code_at_step(secret: str, step: int) -> str:
    """Return the 6-digit TOTP code for *step* (Unix-epoch step, not seconds)."""
    return pyotp.TOTP(secret).at(step * TOTP_PERIOD)


@pytest.fixture()
def secret() -> str:
    """A fresh random TOTP secret for each test."""
    return pyotp.random_base32()


@pytest.fixture()
def pinned_time(monkeypatch: pytest.MonkeyPatch) -> int:
    """Pin time.time to the midpoint of _STEP and return _STEP.

    Patching time.time on the stdlib module object is visible to every module
    that calls time.time(), including verify_totp_code which imports ``time``
    at the top level of snore.auth.totp.
    """
    monkeypatch.setattr("time.time", lambda: float(_STEP * TOTP_PERIOD) + 0.5)
    return _STEP


# ---------------------------------------------------------------------------
# Happy-path window acceptance
# ---------------------------------------------------------------------------


def test_current_step_accepted_returns_current_step(secret, pinned_time):
    """Code from the current step is accepted; matched_step equals the current step."""
    step = pinned_time
    code = _code_at_step(secret, step)
    ok, matched = verify_totp_code(secret, code, None)
    assert ok is True
    assert matched == step


def test_previous_step_accepted_returns_step_minus_one(secret, pinned_time):
    """Code from step-1 (clock-skew tolerance) is accepted; matched_step = current-1."""
    step = pinned_time
    code = _code_at_step(secret, step - 1)
    ok, matched = verify_totp_code(secret, code, None)
    assert ok is True
    assert matched == step - 1


def test_next_step_accepted_returns_step_plus_one(secret, pinned_time):
    """Code from step+1 (clock-skew tolerance) is accepted; matched_step = current+1."""
    step = pinned_time
    code = _code_at_step(secret, step + 1)
    ok, matched = verify_totp_code(secret, code, None)
    assert ok is True
    assert matched == step + 1


# ---------------------------------------------------------------------------
# First-use: last_used_step=None acts as a sentinel allowing any match
# ---------------------------------------------------------------------------


def test_first_use_none_last_used_accepts_any_window(secret, pinned_time):
    """last_used_step=None is the first-use sentinel; any window match is accepted."""
    step = pinned_time
    # The current-step code with last_used_step=None must pass the replay guard.
    code = _code_at_step(secret, step)
    ok, matched = verify_totp_code(secret, code, None)
    assert ok is True
    # Sentinel -1 means matched_step (step) > -1 → accepted.
    assert matched == step


# ---------------------------------------------------------------------------
# Replay guard
# ---------------------------------------------------------------------------


def test_replay_at_same_step_rejected(secret, pinned_time):
    """Code at step N after last_used_step=N is a replay → rejected."""
    step = pinned_time
    code = _code_at_step(secret, step)
    # Simulate: the step N code was already consumed (last_used_step = step).
    ok, _ = verify_totp_code(secret, code, step)
    assert ok is False


def test_replay_at_earlier_step_rejected(secret, pinned_time):
    """Code at step N-1 after last_used_step=N is a replay → rejected."""
    step = pinned_time
    code = _code_at_step(secret, step - 1)
    # last_used_step = step means step-1 <= step → replay.
    ok, _ = verify_totp_code(secret, code, step)
    assert ok is False


def test_fresh_code_after_replay_accepted(secret, pinned_time):
    """After consuming step+1, a code for step+2 (via next window) is accepted.

    This test pins time to step+1 so that step+2 falls within the +1 offset.
    """
    step = pinned_time
    # Repin time to step+1 so step+2 is one window ahead.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("time.time", lambda: float((step + 1) * TOTP_PERIOD) + 0.5)
        code_step2 = _code_at_step(secret, step + 2)
        ok, matched = verify_totp_code(secret, code_step2, step)
        assert ok is True
        assert matched == step + 2


# ---------------------------------------------------------------------------
# Wrong code
# ---------------------------------------------------------------------------


def test_wrong_code_all_windows_rejected(secret, pinned_time):
    """A code from two steps ahead is outside every accepted window → rejected."""
    step = pinned_time
    # step+2 is outside the ±1 window centred on step.
    out_of_window_code = _code_at_step(secret, step + 2)
    ok, _ = verify_totp_code(secret, out_of_window_code, None)
    assert ok is False
