"""Health ingest router — machine-auth push endpoint for Health Auto Export.

Route
-----
POST /api/v1/health/ingest

Auth: ``X-SNORE-Ingest-Token`` request header.  The token is never logged,
echoed, or included in error responses.  Failed verifications are opaque (no
distinction between unknown and revoked) and trigger per-(token_hash, IP)
lockout to slow probing.

This endpoint is exempted from the session-cookie ``AuthMiddleware`` and from
the CSRF check in ``AuthPathMiddleware`` (bearer-token caller, not a browser).
"""

from __future__ import annotations

import json
import logging

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.deps import get_db
from snore.auth.invite_tokens import hash_invite_token
from snore.auth.lockout import LockoutStore
from snore.services.health_import_service import HealthImportService
from snore.services.health_token_service import HealthTokenService
from snore.services.schemas import HealthImportResult

logger = logging.getLogger(__name__)

router = APIRouter()

# Isolated per-(token_hash, IP) lockout store — separate from login/invite
# stores so flooding one endpoint cannot exhaust protection for another.
_ingest_lockout = LockoutStore()

# 10 MiB body ceiling.  HAE payloads are typically small (a few KiB per push),
# but a generous cap accommodates large historical back-fills.
_BODY_LIMIT = 10 * 1024 * 1024


@router.post("/ingest", response_model=HealthImportResult)
async def ingest_health_data(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HealthImportResult:
    """Accept a Health Auto Export JSON push and import health samples.

    Returns the counts of inserted/skipped samples and recomputed summaries.
    """
    # Fast Content-Length check before any DB work — rejects obviously oversized
    # requests without touching the token store.
    raw_cl = request.headers.get("content-length")
    if raw_cl is not None:
        try:
            if int(raw_cl) > _BODY_LIMIT:
                raise HTTPException(status_code=413, detail="Request body too large")
        except ValueError:
            pass  # Malformed Content-Length — enforce via actual body read below.

    raw_token = request.headers.get("x-snore-ingest-token")
    if raw_token is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    ip = get_client_ip(request)
    token_hash = hash_invite_token(raw_token)

    if _ingest_lockout.is_locked(token_hash, ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    profile_id = await HealthTokenService(db).verify(raw_token)
    if profile_id is None:
        _ingest_lockout.record_failure(token_hash, ip)
        raise HTTPException(status_code=401, detail="Authentication required")

    # Commit the last_used_at update immediately so this connection releases
    # the SQLite write lock before the import service opens its own
    # BEGIN IMMEDIATE transactions.  Holding the lock across the full import
    # (many chunked writes) would exhaust busy_timeout on each chunk.
    await db.commit()

    # Read body and enforce ceiling on actual bytes received.
    body = await request.body()
    if len(body) > _BODY_LIMIT:
        raise HTTPException(status_code=413, detail="Request body too large")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Request body must be a JSON object"
        )

    return await HealthImportService().import_payload(payload, profile_id)
