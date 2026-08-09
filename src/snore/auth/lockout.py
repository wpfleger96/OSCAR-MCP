"""In-memory per-account + per-IP lockout for auth endpoints.

Implements exponential back-off: each consecutive failure doubles the
lockout window up to ``MAX_LOCKOUT_SECONDS``.  Keyed by (canonical_email,
ip_address) so a distributed brute-force from many IPs does not lock out
the account for legitimate users at different IPs, and a single IP trying
many emails does not create global noise.

This is a process-local, in-memory store — the hobby-scale control.  It
resets on process restart and is not shared across workers (one-worker
deployment constraint is documented in the plan).
"""

from __future__ import annotations

import itertools
import threading
import time

from collections import deque
from dataclasses import dataclass

from snore.auth.emails import normalize_email

# Maximum lockout duration in seconds (15 minutes).
MAX_LOCKOUT_SECONDS: float = 15 * 60

# Minimum lockout duration after the first failure (5 seconds).
BASE_LOCKOUT_SECONDS: float = 5.0

# Maximum number of lockout entries before oldest are evicted.
MAX_ENTRIES: int = 10_000


@dataclass
class _LockoutEntry:
    failures: int = 0
    locked_until: float = 0.0  # monotonic seconds


class LockoutStore:
    """Thread-safe in-memory lockout tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _LockoutEntry] = {}
        self._evict_cursor: int = 0

    def is_locked(self, email: str, ip: str) -> bool:
        """Return True if the (email, ip) pair is currently locked out."""
        key = (normalize_email(email), ip)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            return time.monotonic() < entry.locked_until

    def record_failure(self, email: str, ip: str) -> None:
        """Record an authentication failure and update the lockout window.

        When the store is at ``MAX_ENTRIES`` and all entries are still active
        (no expired slot to reclaim), the new key is silently dropped rather
        than evicting an active lockout — evicting an active lockout would
        turn a flood into an auth bypass.
        """
        key = (normalize_email(email), ip)
        now = time.monotonic()
        with self._lock:
            if key in self._entries:
                # Update existing entry unconditionally.
                pass
            elif len(self._entries) >= MAX_ENTRIES:
                # At cap for a new key: try to free an expired slot.
                self._evict_one_expired(now)
                if len(self._entries) >= MAX_ENTRIES:
                    # No expired entry found — drop without evicting an active lockout.
                    return

            entry = self._entries.setdefault(key, _LockoutEntry())
            entry.failures += 1
            # Exponential back-off: BASE * 2^(failures-1), capped at MAX.
            lockout = min(
                BASE_LOCKOUT_SECONDS * (2 ** (entry.failures - 1)),
                MAX_LOCKOUT_SECONDS,
            )
            entry.locked_until = now + lockout

    def record_success(self, email: str, ip: str) -> None:
        """Clear the lockout state after a successful authentication."""
        key = (normalize_email(email), ip)
        with self._lock:
            self._entries.pop(key, None)

    def _evict_one_expired(self, now: float) -> None:
        """Evict one expired entry starting from _evict_cursor. Must hold ``_lock``.

        Advances the cursor by 1 when an entry is evicted, or by n (full lap)
        when no expired entry is found, so every position is examined across
        bounded calls under sustained load.
        """
        n = len(self._entries)
        if n == 0:
            return
        start = self._evict_cursor % n
        for i in range(n):
            k = next(itertools.islice(self._entries, (start + i) % n, None))
            v = self._entries[k]
            if now >= v.locked_until:
                del self._entries[k]
                self._evict_cursor = start + i + 1
                return
        # Full lap without finding an expired entry.
        self._evict_cursor = start + n


# Separate module-level singletons: login lockout and invite-token lockout.
# Keeping them separate prevents flooding the invite endpoint from exhausting
# the login lockout store and silently disabling login protection.
_lockout_store = LockoutStore()
_invite_lockout_store = LockoutStore()


def get_lockout_store() -> LockoutStore:
    return _lockout_store


def get_invite_lockout_store() -> LockoutStore:
    return _invite_lockout_store


# ---------------------------------------------------------------------------
# Per-IP sliding-window rate limiter for public auth endpoints
# ---------------------------------------------------------------------------

# Number of requests per IP allowed within the window before 429.
RATE_WINDOW_SECONDS: float = 60.0
RATE_MAX_PER_WINDOW: int = 30
RATE_MAX_IPS: int = 10_000


class RateLimitStore:
    """Thread-safe per-IP sliding-window counter for public auth endpoints.

    When the IP table is full, new IPs are allowed without tracking rather
    than refusing them — being a DoS amplifier is worse than losing coarse
    rate control.
    """

    def __init__(
        self,
        window: float = RATE_WINDOW_SECONDS,
        max_per_window: int = RATE_MAX_PER_WINDOW,
        max_ips: int = RATE_MAX_IPS,
    ) -> None:
        self._window = window
        self._max = max_per_window
        self._max_ips = max_ips
        self._entries: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        # Persistent cursor for _purge_stale: advances 64 slots per call so
        # every position in the table is examined within ceil(n/64) calls
        # regardless of where active entries are clustered.
        self._purge_cursor: int = 0

    def check_and_record(self, ip: str) -> bool:
        """Return True (allowed) or False (rate-limited); records allowed requests.

        Before applying the capacity ceiling, performs a bounded purge of
        expired and empty IP entries so the table recovers from saturation
        when old tracking windows close.  This prevents the table from filling
        permanently and thereafter allowing every new IP untracked forever.
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if ip not in self._entries:
                if len(self._entries) >= self._max_ips:
                    # Try to reclaim stale entries before declaring the table full.
                    self._purge_stale(cutoff, budget=64)
                if len(self._entries) >= self._max_ips:
                    # Still at capacity — allow without tracking rather than
                    # becoming a DoS amplifier.
                    return True
                self._entries[ip] = deque()
            times = self._entries[ip]
            # Prune expired timestamps from this IP's window.
            while times and times[0] < cutoff:
                times.popleft()
            if len(times) >= self._max:
                return False
            times.append(now)
            return True

    def _purge_stale(self, cutoff: float, budget: int) -> None:
        """Evict IPs whose tracking windows have fully expired.

        Examines exactly ``budget`` entries starting from ``_purge_cursor``
        and wraps around the table.  Advancing the cursor each call ensures
        every position is eventually examined regardless of where active
        entries are clustered — a prefix of 9,936 active IPs does not pin
        the cursor to the front and leave the stale tail unexamined.

        Uses ``itertools.islice`` over the dict view rather than materialising
        a full key list so the O(n) allocation is avoided under the lock.

        Must be called with ``_lock`` held.
        """
        if not self._entries:
            return
        keys_view = self._entries
        n = len(keys_view)
        start = self._purge_cursor % n
        stale: list[str] = []
        for i in range(min(budget, n)):
            ip_key = next(itertools.islice(keys_view, (start + i) % n, None))
            times = self._entries.get(ip_key)
            if times is None:
                continue  # deleted earlier in this sweep
            while times and times[0] < cutoff:
                times.popleft()
            if not times:
                stale.append(ip_key)
        # Advance the cursor by the number of positions examined.
        self._purge_cursor = (start + min(budget, n)) % max(n, 1)
        for ip_key in stale:
            self._entries.pop(ip_key, None)


_rate_limit_store = RateLimitStore()


def get_rate_limit_store() -> RateLimitStore:
    return _rate_limit_store
