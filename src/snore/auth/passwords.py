"""Argon2id password hashing via argon2-cffi.

Uses a maintained-facade style: verification transparently rehashes with
current parameters when the stored hash was produced with older settings.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# A single shared hasher with sensible defaults.
# argon2-cffi defaults (time_cost=3, memory_cost=65536, parallelism=4) are
# strong enough for hobby use; tune these at the SaaS milestone.
_hasher = PasswordHasher()

# Maximum password length (bytes) before hashing.  Argon2 has no silent
# truncation, but we cap here to prevent DoS via enormous payloads.
MAX_PASSWORD_BYTES = 1024


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    Args:
        password: Plaintext password string.

    Returns:
        Encoded Argon2id hash string suitable for storage.

    Raises:
        ValueError: If the password exceeds MAX_PASSWORD_BYTES.
    """
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes encoded")
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, str | None]:
    """Verify a password against a stored Argon2id hash.

    Returns:
        ``(True, new_hash)`` where ``new_hash`` is a freshly-computed hash
        if the stored hash was produced with outdated parameters (rehash
        policy), otherwise ``None``.

        ``(False, None)`` on mismatch.
    """
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False, None
    except (VerificationError, InvalidHashError):
        return False, None

    # Rehash if the stored hash used old parameters.
    new_hash: str | None = None
    if _hasher.check_needs_rehash(stored_hash):
        new_hash = _hasher.hash(password)

    return True, new_hash


def dummy_verify() -> None:
    """Perform a dummy Argon2id verification to equalize timing for unknown emails.

    Callers should invoke this on the missing-user branch so that
    "email not found" and "wrong password" take roughly the same time.
    """
    try:
        _hasher.verify(
            "$argon2id$v=19$m=65536,t=3,p=4$"
            "dGVzdHNhbHQxMjM0NTY3OA$dummyhashXXXXXXXXXXXXXXXXXXXXXXX",
            "dummy_password",
        )
    except Exception:
        pass  # Expected — the hash above is invalid; the timing is what matters.
