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

import threading
import time

from dataclasses import dataclass

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

    def is_locked(self, email: str, ip: str) -> bool:
        """Return True if the (email, ip) pair is currently locked out."""
        key = (email.lower().strip(), ip)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            return time.monotonic() < entry.locked_until

    def record_failure(self, email: str, ip: str) -> None:
        """Record an authentication failure and update the lockout window."""
        key = (email.lower().strip(), ip)
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= MAX_ENTRIES and key not in self._entries:
                # Evict the oldest expired entry to cap memory usage.
                self._evict_one_expired(now)

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
        key = (email.lower().strip(), ip)
        with self._lock:
            self._entries.pop(key, None)

    def _evict_one_expired(self, now: float) -> None:
        """Evict the first expired entry found. Must hold ``_lock``."""
        for k, v in list(self._entries.items()):
            if now >= v.locked_until:
                del self._entries[k]
                return


# Module-level singleton — one store for the whole process.
_lockout_store = LockoutStore()


def get_lockout_store() -> LockoutStore:
    return _lockout_store
