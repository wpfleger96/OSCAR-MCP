"""Invite token hashing — shared helper used by auth router, admin router, and CLI."""

import hashlib


def hash_invite_token(raw: str) -> str:
    """Return the SHA-256 hex digest of a raw invite token."""
    return hashlib.sha256(raw.encode()).hexdigest()
