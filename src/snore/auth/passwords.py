"""Argon2id password hashing via argon2-cffi.

Uses a maintained-facade style: verification transparently rehashes with
current parameters when the stored hash was produced with older settings.

Concurrency model
-----------------
KDF operations (Argon2id hash and verify) run inside a dedicated
``ThreadPoolExecutor`` with ``max_workers=4``.  The executor's own worker
count is the admission bound — submitting more than 4 jobs queues them inside
the executor rather than starting new threads.  Critically, this bound is owned
by the thread doing the work, **not** by the coroutine awaiting it.  Cancelling
an awaiting request does not release the executor slot; the thread runs to
completion and the slot is freed when the native Argon2 operation finishes.
This prevents an adversary from defeating the memory ceiling by issuing and
cancelling requests faster than the semaphore can be freed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

# A single shared hasher with sensible defaults.
# argon2-cffi defaults (time_cost=3, memory_cost=65536, parallelism=4) are
# strong enough for hobby use; tune these at the SaaS milestone.
_hasher = PasswordHasher()

# Maximum password length (bytes) before hashing.  Argon2 has no silent
# truncation, but we cap here to prevent DoS via enormous payloads.
MAX_PASSWORD_BYTES = 1024

# Dedicated bounded executor for KDF operations.  max_workers=4 caps
# concurrent Argon2 ops at ~256 MiB peak memory.  The executor slot is held
# by the running thread, not by the awaiting coroutine, so task cancellation
# cannot free a slot while its underlying Argon2 computation is still live.
_KDF_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="snore-kdf-",
)


def validate_password_bytes(password: str) -> None:
    """Raise ValueError if the password is empty or exceeds MAX_PASSWORD_BYTES encoded.

    This is the shared boundary for all KDF callers — both login and invite
    redemption call this before any Argon2 operation.

    Invariant: 1 ≤ len(password.encode()) ≤ 1024.
    """
    encoded_len = len(password.encode())
    if encoded_len == 0:
        raise ValueError("Password must be at least 1 byte")
    if encoded_len > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes encoded")


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    Raises:
        ValueError: If the password is empty or exceeds MAX_PASSWORD_BYTES.
    """
    validate_password_bytes(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, str | None]:
    """Verify a password against a stored Argon2id hash.

    Returns:
        ``(True, new_hash)`` if the password matches (new_hash is set when
        the stored hash used outdated parameters); ``(False, None)`` on mismatch.
    """
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False, None
    except (VerificationError, InvalidHashError):
        return False, None

    new_hash: str | None = None
    if _hasher.check_needs_rehash(stored_hash):
        new_hash = _hasher.hash(password)

    return True, new_hash


def dummy_verify() -> None:
    """Perform a dummy Argon2id verification to equalize timing for unknown emails."""
    try:
        _hasher.verify(
            "$argon2id$v=19$m=65536,t=3,p=4$"
            "dGVzdHNhbHQxMjM0NTY3OA$dummyhashXXXXXXXXXXXXXXXXXXXXXXX",
            "dummy_password",
        )
    except Exception:
        pass  # Expected — the hash is deliberately invalid; timing is what matters.


# ---------------------------------------------------------------------------
# Async wrappers — run KDF operations in the bounded _KDF_EXECUTOR so Argon2
# never blocks the event loop.  The executor slot is owned by the thread, not
# the awaiting coroutine, so task cancellation cannot start a 5th native op.
# ---------------------------------------------------------------------------


async def hash_password_async(password: str) -> str:
    """Hash password in the KDF executor; raises ValueError on byte-limit violation."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_KDF_EXECUTOR, hash_password, password)


async def verify_password_async(
    stored_hash: str, password: str
) -> tuple[bool, str | None]:
    """Verify password in the KDF executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _KDF_EXECUTOR, verify_password, stored_hash, password
    )


async def dummy_verify_async() -> None:
    """Run dummy Argon2 verification in the KDF executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_KDF_EXECUTOR, dummy_verify)
