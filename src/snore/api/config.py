"""Application configuration resolved from environment variables.

All configuration is read once at startup via ``load_config()`` and stored
in a module-level ``AppConfig`` instance.  Code accesses the config through
``get_config()`` — never by reading ``os.environ`` directly.

Environment variables
---------------------
``SNORE_AUTH_MODE``
    ``"multiuser"`` (default, fail-closed) or ``"local"``.
    ``just dev`` passes ``local`` explicitly.

``SNORE_SESSION_SECRET``
    Required in multiuser mode.  Minimum 32 characters.  Used to sign the
    session cookie with ItsDangerous.  Never logged.

``SNORE_PUBLIC_BASE_URL``
    Required in multiuser mode.  Must be a loopback HTTP URL or any HTTPS
    URL.  Drives the ``Secure`` cookie attribute and CSRF origin check.
    Example: ``http://127.0.0.1:8000`` (dev-auth) or ``https://snore.example.com``.
    Must not contain userinfo, path, query string, fragment, or an invalid/out-
    of-range port — these would cause the configured origin to disagree with
    the ``Origin`` header browsers actually send.

``SNORE_BIND_HOST``
    The host uvicorn binds to (default ``"127.0.0.1"``).  Startup refuses
    ``local`` mode combined with a non-loopback bind so that local mode is
    not accidentally exposed on a LAN or public interface.

``SNORE_TRUSTED_PROXIES``
    Comma-separated list of trusted proxy IP addresses.  ``cf-connecting-ip``
    is honoured only when the immediate peer is in this list.

``SNORE_DEV_ORIGINS``
    Comma-separated list of additional origins allowed by the CSRF middleware.
    For development only (e.g. ``http://localhost:5173`` when the Vite dev
    server runs on a separate port).  Every entry is validated at startup;
    malformed entries raise ``ConfigError``.  Never set in production.

``SNORE_MAX_UPLOAD_BYTES``
    Per-upload ingress byte ceiling, enforced in the ASGI receive stream before
    any parser spooling begins.  Default: 512 MiB.  Accepts integer bytes.

``SNORE_MAX_FILE_BYTES``
    Per-file size limit applied after multipart spooling.  Default: 256 MiB.
    Accepts integer bytes.  Prevents a single large file from consuming the
    entire per-upload quota.

``SNORE_MAX_JOBS_PER_USER``
    Maximum active (PENDING_UPLOAD + PENDING + RUNNING) jobs per user.
    Default: 3.

``SNORE_MAX_JOBS_GLOBAL``
    Maximum active jobs across all users.  Default: 10.
"""

from __future__ import annotations

import ipaddress
import os

from dataclasses import dataclass
from urllib.parse import urlparse

from snore.auth.actor import AuthMode


class ConfigError(ValueError):
    """Raised when the application configuration is invalid."""


def parse_origin(url: str) -> tuple[str, str, int] | None:
    """Parse ``url`` to a canonical ``(scheme, host, effective_port)`` tuple.

    Returns ``None`` when the URL cannot be parsed to a valid origin.  Never
    raises — invalid inputs produce ``None`` so callers get consistent
    comparison semantics.

    This is the single origin-parsing function used by config validation, the
    CSRF middleware, and dev-origin pre-parsing so all three work from the same
    algorithm.
    """
    try:
        p = urlparse(url)
        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()
        if not scheme or not host:
            return None
        port = p.port  # raises ValueError on malformed port text
        if port is None:
            port = 443 if scheme == "https" else 80
        if not 1 <= port <= 65535:
            return None
        return (scheme, host, port)
    except Exception:
        return None


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    auth_mode: AuthMode
    session_secret: str  # Empty string in local mode; required in multiuser.
    public_base_url: str  # Validated URL or empty string in local mode.
    public_origin: (
        tuple[str, str, int] | None
    )  # Parsed once at load; None in local mode.
    bind_host: str
    trusted_proxies: frozenset[str]
    dev_origins: frozenset[tuple[str, str, int]]  # Pre-parsed; validated at startup.
    # Upload / job resource bounds
    max_upload_bytes: int  # Per-upload ingress ceiling (bytes); default 512 MiB.
    max_file_bytes: int  # Per-file size limit (bytes); default 256 MiB.
    max_jobs_per_user: int  # Per-user active-job cap; default 3.
    max_jobs_global: int  # Global active-job cap; default 10.

    @property
    def is_multiuser(self) -> bool:
        return self.auth_mode is AuthMode.MULTIUSER

    @property
    def secure_cookie(self) -> bool:
        """True when the public base URL is HTTPS (non-loopback).

        Derives from the pre-parsed ``public_origin`` field so the same parsed
        value is used for both cookie attributes and CSRF comparison.
        ``Secure`` is off only for loopback HTTP (``just dev-auth`` over plain
        local HTTP); any non-loopback public URL must be HTTPS.
        """
        if self.public_origin is None:
            return False
        scheme, host, _ = self.public_origin
        if scheme == "https":
            return True
        # HTTP is allowed only for loopback.
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host not in ("localhost", "localhost.localdomain")


_config: AppConfig | None = None


def load_config(
    *,
    auth_mode_override: str | None = None,
    bind_host_override: str | None = None,
) -> AppConfig:
    """Read environment variables and return a validated ``AppConfig``.

    Args:
        auth_mode_override:  Used by tests to inject a mode without env pollution.
        bind_host_override:  Used by tests to inject a bind host.

    Raises:
        ConfigError: If required configuration is missing or invalid.
    """
    raw_mode = auth_mode_override or os.environ.get("SNORE_AUTH_MODE", "multiuser")
    try:
        auth_mode = AuthMode(raw_mode.lower())
    except ValueError as exc:
        raise ConfigError(
            f"SNORE_AUTH_MODE must be 'local' or 'multiuser', got {raw_mode!r}"
        ) from exc

    session_secret = os.environ.get("SNORE_SESSION_SECRET", "")
    public_base_url = os.environ.get("SNORE_PUBLIC_BASE_URL", "")
    bind_host = bind_host_override or os.environ.get("SNORE_BIND_HOST", "127.0.0.1")
    raw_proxies = os.environ.get("SNORE_TRUSTED_PROXIES", "")
    trusted_proxies = frozenset(p.strip() for p in raw_proxies.split(",") if p.strip())

    # Parse and validate dev origins at startup; malformed entries are a ConfigError.
    raw_dev = os.environ.get("SNORE_DEV_ORIGINS", "")
    dev_origins: set[tuple[str, str, int]] = set()
    for raw_origin in raw_dev.split(","):
        raw_origin = raw_origin.strip()
        if not raw_origin:
            continue
        parsed_origin = parse_origin(raw_origin)
        if parsed_origin is None:
            raise ConfigError(
                f"SNORE_DEV_ORIGINS contains an invalid origin: {raw_origin!r}. "
                "Expected format: http://hostname or https://hostname[:port]"
            )
        dev_origins.add(parsed_origin)

    # Resource bounds — read with safe int parsing.
    try:
        max_upload_bytes = int(
            os.environ.get("SNORE_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))
        )
    except ValueError as exc:
        raise ConfigError(
            "SNORE_MAX_UPLOAD_BYTES must be a positive integer (bytes)"
        ) from exc
    if max_upload_bytes <= 0:
        raise ConfigError("SNORE_MAX_UPLOAD_BYTES must be a positive integer (bytes)")

    try:
        max_file_bytes = int(
            os.environ.get("SNORE_MAX_FILE_BYTES", str(256 * 1024 * 1024))
        )
    except ValueError as exc:
        raise ConfigError(
            "SNORE_MAX_FILE_BYTES must be a positive integer (bytes)"
        ) from exc
    if max_file_bytes <= 0:
        raise ConfigError("SNORE_MAX_FILE_BYTES must be a positive integer (bytes)")

    try:
        max_jobs_per_user = int(os.environ.get("SNORE_MAX_JOBS_PER_USER", "3"))
    except ValueError as exc:
        raise ConfigError("SNORE_MAX_JOBS_PER_USER must be a positive integer") from exc
    if max_jobs_per_user <= 0:
        raise ConfigError("SNORE_MAX_JOBS_PER_USER must be a positive integer")

    try:
        max_jobs_global = int(os.environ.get("SNORE_MAX_JOBS_GLOBAL", "10"))
    except ValueError as exc:
        raise ConfigError("SNORE_MAX_JOBS_GLOBAL must be a positive integer") from exc
    if max_jobs_global <= 0:
        raise ConfigError("SNORE_MAX_JOBS_GLOBAL must be a positive integer")

    public_origin: tuple[str, str, int] | None = None
    if auth_mode is AuthMode.MULTIUSER:
        if not session_secret:
            raise ConfigError(
                "SNORE_SESSION_SECRET is required in multiuser mode. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(session_secret) < 32:
            raise ConfigError(
                "SNORE_SESSION_SECRET must be at least 32 characters long."
            )
        if not public_base_url:
            raise ConfigError(
                "SNORE_PUBLIC_BASE_URL is required in multiuser mode. "
                "Example: http://127.0.0.1:8000 or https://snore.example.com"
            )
        _validate_public_base_url(public_base_url)
        # Parse once; all consumers (CSRF, secure_cookie) use this value.
        public_origin = parse_origin(public_base_url)
        if public_origin is None:
            # _validate_public_base_url passed, so this should not happen.
            raise ConfigError(
                f"SNORE_PUBLIC_BASE_URL {public_base_url!r} could not be parsed "
                "to a canonical origin — unexpected internal error"
            )

    if auth_mode is AuthMode.LOCAL:
        # Refuse local mode on a non-loopback bind — it would expose the
        # auto-provisioned admin account without any authentication.
        try:
            addr = ipaddress.ip_address(bind_host)
            if not addr.is_loopback:
                raise ConfigError(
                    f"Local auth mode is not allowed on non-loopback bind address "
                    f"{bind_host!r}. Either use SNORE_AUTH_MODE=multiuser or bind "
                    f"to a loopback address (127.0.0.1)."
                )
        except ValueError:
            # Non-numeric host — allow "localhost" only.
            if bind_host not in ("localhost", "localhost.localdomain"):
                raise ConfigError(
                    f"Local auth mode is not allowed on non-loopback bind host "
                    f"{bind_host!r}."
                ) from None

    return AppConfig(
        auth_mode=auth_mode,
        session_secret=session_secret,
        public_base_url=public_base_url,
        public_origin=public_origin,
        bind_host=bind_host,
        trusted_proxies=trusted_proxies,
        dev_origins=frozenset(dev_origins),
        max_upload_bytes=max_upload_bytes,
        max_file_bytes=max_file_bytes,
        max_jobs_per_user=max_jobs_per_user,
        max_jobs_global=max_jobs_global,
    )


def _validate_public_base_url(url: str) -> None:
    """Raise ConfigError if ``url`` is not a valid loopback HTTP or HTTPS URL.

    Rejects userinfo, path, query string, fragment, malformed ports, and
    out-of-range ports (0 or >65535) so that the value can be used as a
    canonical origin without ambiguity.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ConfigError(f"SNORE_PUBLIC_BASE_URL is not a valid URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ConfigError(
            f"SNORE_PUBLIC_BASE_URL must use http or https, got {parsed.scheme!r}"
        )

    host = parsed.hostname or ""
    if not host:
        raise ConfigError("SNORE_PUBLIC_BASE_URL must include a host")

    # Reject userinfo (username/password in the URL).
    if parsed.username or parsed.password:
        raise ConfigError(
            "SNORE_PUBLIC_BASE_URL must not contain userinfo (user:pass@host)"
        )

    # Reject path, query, and fragment.
    if parsed.path not in ("", "/"):
        raise ConfigError(
            f"SNORE_PUBLIC_BASE_URL must not include a path, got {parsed.path!r}"
        )
    if parsed.query:
        raise ConfigError("SNORE_PUBLIC_BASE_URL must not include a query string")
    if parsed.fragment:
        raise ConfigError("SNORE_PUBLIC_BASE_URL must not include a fragment")

    # Validate port — reject malformed text and out-of-range values.
    try:
        port = parsed.port  # raises ValueError for non-numeric port text
    except ValueError as exc:
        raise ConfigError(f"SNORE_PUBLIC_BASE_URL has an invalid port: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ConfigError(f"SNORE_PUBLIC_BASE_URL port must be 1–65535, got {port}")

    if parsed.scheme == "http":
        # HTTP is only allowed for loopback addresses.
        try:
            addr = ipaddress.ip_address(host)
            if not addr.is_loopback:
                raise ConfigError(
                    f"SNORE_PUBLIC_BASE_URL with http:// must be a loopback address "
                    f"(127.x.x.x or ::1), got {host!r}. Use https:// for non-loopback."
                )
        except ValueError:
            # Non-numeric host; accept localhost only.
            if host not in ("localhost", "localhost.localdomain"):
                raise ConfigError(
                    f"SNORE_PUBLIC_BASE_URL with http:// must be localhost or a "
                    f"loopback IP, got {host!r}."
                ) from None


def get_config() -> AppConfig:
    """Return the current application config, loading it lazily if not yet set.

    In production the lifespan calls ``set_config()`` before serving.
    Tests that need a specific config call ``set_config()`` directly.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: AppConfig) -> None:
    """Set the global config — used by the lifespan and tests."""
    global _config
    _config = cfg


def reset_config() -> None:
    """Clear the cached config — used by tests only."""
    global _config
    _config = None
