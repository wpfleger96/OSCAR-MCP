"""TOTP (RFC 6238) service — secret generation, code verification, and recovery codes.

All TOTP operations are pure functions except ``redeem_recovery_code``, which
accepts an ``AsyncSession`` and executes within the caller's transaction.
No commit is performed here; callers commit when the full request succeeds.

Never log TOTP secrets or raw recovery codes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from datetime import UTC, datetime

import pyotp
import segno

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import TotpRecoveryCode

TOTP_PENDING_SALT = "snore-totp-pending-v1"
TOTP_PENDING_MAX_AGE = 300  # seconds
TOTP_ISSUER = "SNORE"
TOTP_PERIOD = 30
RECOVERY_CODE_COUNT = 10


def generate_totp_secret() -> str:
    """Generate a random Base32 TOTP secret via pyotp."""
    return pyotp.random_base32()


def build_provisioning_uri(secret: str, email: str) -> str:
    """Build an ``otpauth://`` URI for authenticator app enrollment.

    Emits SHA1 / 6-digit / 30-second defaults via pyotp so all standard
    authenticator apps (Google Authenticator, Authy, 1Password, etc.) parse it
    without extra configuration.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def build_qr_data_uri(uri: str) -> str:
    """Return a ``data:image/svg+xml`` URI for the provisioning URI QR code.

    The returned value can be used directly as an ``<img src>`` attribute —
    no external server request is needed.
    """
    return segno.make(uri, error="m").svg_data_uri()


def verify_totp_code(
    secret: str, code: str, last_used_step: int | None
) -> tuple[bool, int]:
    """Verify a TOTP code against *secret* with replay protection.

    Accepts codes from the previous, current, or next 30-second window to
    tolerate reasonable clock skew between client and server.  The matched
    time-step is returned so the caller can persist it for replay prevention.

    All three offsets are always scanned (no early break) to keep timing
    uniform regardless of which window the code falls in.

    Args:
        secret:          Base32 TOTP secret.
        code:            6-digit string supplied by the user.
        last_used_step:  Most recently verified time-step stored in the DB, or
                         ``None`` for the first verification.

    Returns:
        ``(True, matched_step)`` on success; ``(False, current_step)`` on
        mismatch or replay.
    """
    now = time.time()
    current_step = int(now // TOTP_PERIOD)
    totp = pyotp.TOTP(secret)

    matched_step: int | None = None
    for offset in (-1, 0, 1):
        expected = totp.at(int(now) + offset * TOTP_PERIOD)
        if hmac.compare_digest(expected, code):
            matched_step = current_step + offset

    if matched_step is None:
        return False, current_step

    # Replay guard: reject a step that was already accepted.
    if matched_step <= (last_used_step if last_used_step is not None else -1):
        return False, current_step

    return True, matched_step


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Generate *count* one-time recovery codes (10 lowercase hex chars each)."""
    return [secrets.token_hex(5) for _ in range(count)]


def hash_recovery_code(raw: str) -> str:
    """Return the SHA-256 hex digest of a raw recovery code.

    Mirrors the invite-token hashing convention in ``invite_tokens.py``.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


async def redeem_recovery_code(db: AsyncSession, user_id: int, raw: str) -> bool:
    """Mark a recovery code as used if it exists and is unused for *user_id*.

    Runs inside the caller's request transaction — no commit is performed here.

    Returns:
        ``True`` if the code was valid and has been marked used; ``False`` if
        the code does not exist, belongs to a different user, or was already used.
    """
    code_hash = hash_recovery_code(raw)
    result = await db.execute(
        select(TotpRecoveryCode).where(
            TotpRecoveryCode.user_id == user_id,
            TotpRecoveryCode.code_hash == code_hash,
            TotpRecoveryCode.used_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    row.used_at = datetime.now(UTC)
    return True


def is_totp_code(code: str) -> bool:
    """Return True if *code* is exactly 6 ASCII decimal digits."""
    return len(code) == 6 and code.isascii() and code.isdigit()


def is_recovery_code(code: str) -> bool:
    """Return True if *code* is exactly 10 lowercase hexadecimal characters."""
    return (
        len(code) == 10
        and code.isascii()
        and all(c in "0123456789abcdef" for c in code)
    )


def _make_pending_signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret, salt=TOTP_PENDING_SALT)


def encode_totp_pending_token(secret: str, user_id: int) -> str:
    """Produce a time-limited signed token embedding *user_id*.

    Used to tie a pending TOTP setup (secret generation) to the authenticated
    user across the enrollment confirmation step without storing the TOTP
    secret in a cookie or the session.

    Args:
        secret:   SNORE session secret (``AppConfig.session_secret``).
        user_id:  ID of the user completing enrollment.

    Returns:
        An opaque URL-safe string valid for ``TOTP_PENDING_MAX_AGE`` seconds.
    """
    return _make_pending_signer(secret).sign(str(user_id)).decode()


def decode_totp_pending_token(secret: str, token: str) -> int | None:
    """Validate and decode a pending TOTP token, returning the user ID.

    Args:
        secret:  SNORE session secret used to sign the token.
        token:   Token produced by ``encode_totp_pending_token``.

    Returns:
        The embedded user ID as an ``int`` if the token is valid and unexpired;
        ``None`` on ``BadSignature``, ``SignatureExpired``, or ``ValueError``.
    """
    try:
        raw = (
            _make_pending_signer(secret)
            .unsign(token.encode(), max_age=TOTP_PENDING_MAX_AGE)
            .decode()
        )
        return int(raw)
    except (BadSignature, SignatureExpired, ValueError):
        return None
