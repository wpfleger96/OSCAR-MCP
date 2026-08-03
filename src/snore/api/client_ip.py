"""Canonical trusted-client IP resolution for rate limiting and lockout keying.

This module is the single source of truth for extracting the real client IP
from an HTTP request.  Both ``CsrfMiddleware``/``RateLimitMiddleware`` (in
``middleware.py``) and the auth router import this helper so they always derive
the same key from the same algorithm.

``get_client_ip()`` honours ``SNORE_TRUSTED_PROXIES``: if the immediate peer
is in the trusted-proxy list, it accepts ``cf-connecting-ip`` — but only after
validating that the forwarded value is a well-formed IP address.  Malformed or
missing forwarded values fall back to the peer address.  The forwarded value is
never used as a lockout key unless it parses as a valid IP.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.requests import Request

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """Return the trusted client IP address for rate-limiting and lockout keying.

    Uses ``SNORE_TRUSTED_PROXIES`` to decide whether to trust
    ``cf-connecting-ip``.  The forwarded value is validated as a well-formed IP
    before use; invalid values fall back to the peer address.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    peer = request.client.host if request.client else "unknown"
    cfg = get_config()
    if peer in cfg.trusted_proxies:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            try:
                ipaddress.ip_address(forwarded)
                return forwarded
            except ValueError:
                logger.warning(
                    "cf-connecting-ip %r is not a valid IP address; using peer %r",
                    forwarded,
                    peer,
                )
    return peer
