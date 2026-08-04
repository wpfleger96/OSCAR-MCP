"""Canonical trusted-client IP resolution for rate limiting and lockout keying.

This module is the single source of truth for extracting the real client IP
from an HTTP request.  Both ``AuthPathMiddleware``/``RateLimitMiddleware`` (in
``middleware.py``) and the auth router import this helper so they always derive
the same key from the same algorithm.

``get_client_ip()`` honours ``SNORE_TRUSTED_PROXIES``: if the immediate peer
is in the trusted-proxy list, it probes forwarded-IP headers in order:

1. ``cf-connecting-ip`` — Cloudflare's canonical single-IP header.
2. ``x-forwarded-for`` — nginx / HAProxy / AWS ALB standard; the rightmost
   value is taken (least likely to be attacker-controlled in a typical
   single-proxy deployment).
3. ``x-real-ip`` — nginx single-IP header.

Each candidate is validated as a well-formed IP address before use.  Malformed
or missing values fall through to the next header, and ultimately to the peer
address.  The forwarded value is never used as a lockout key unless it parses
as a valid IP.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.requests import Request

logger = logging.getLogger(__name__)


def _parse_forwarded_ip(value: str) -> str | None:
    """Return the canonical IP string if *value* is a well-formed IP, else None."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return str(ipaddress.ip_address(stripped))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Return the trusted client IP address for rate-limiting and lockout keying.

    When the immediate peer is in ``SNORE_TRUSTED_PROXIES``, forwarded-IP
    headers are probed in order: ``cf-connecting-ip``, then the rightmost
    entry in ``x-forwarded-for``, then ``x-real-ip``.  Each candidate is
    validated as a well-formed IP before use; invalid or absent values fall
    through to the next header and ultimately to the peer address.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    raw_peer = request.client.host if request.client else "unknown"
    # Normalise the peer address to canonical form so equivalent IPv6
    # spellings (e.g. ``2001:0db8::1`` vs ``2001:db8::1``) map to the
    # same rate-limit and lockout key.
    try:
        peer = str(ipaddress.ip_address(raw_peer))
    except ValueError:
        peer = raw_peer

    cfg = get_config()
    if raw_peer in cfg.trusted_proxies or peer in cfg.trusted_proxies:
        # 1. Cloudflare single-IP header.
        cf = request.headers.get("cf-connecting-ip", "")
        if ip := _parse_forwarded_ip(cf):
            return ip
        if cf.strip():
            logger.warning(
                "cf-connecting-ip %r is not a valid IP address; trying XFF", cf
            )

        # 2. X-Forwarded-For: take the rightmost entry (least attacker-controlled
        #    in a single-proxy deployment).
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            last = xff.rsplit(",", 1)[-1]
            if ip := _parse_forwarded_ip(last):
                return ip
            logger.warning(
                "x-forwarded-for rightmost entry %r is not a valid IP; trying X-Real-IP",
                last.strip(),
            )

        # 3. X-Real-IP: nginx single-IP header.
        xri = request.headers.get("x-real-ip", "")
        if xri.strip():
            if ip := _parse_forwarded_ip(xri):
                return ip
            logger.warning(
                "x-real-ip %r is not a valid IP address; using peer %r", xri, peer
            )

    return peer
