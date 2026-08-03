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

``SNORE_BIND_HOST``
    The host uvicorn binds to (default ``"127.0.0.1"``).  Startup refuses
    ``local`` mode combined with a non-loopback bind so that local mode is
    not accidentally exposed on a LAN or public interface.

``SNORE_TRUSTED_PROXIES``
    Comma-separated list of trusted proxy IP addresses.  ``cf-connecting-ip``
    is honoured only when the immediate peer is in this list.

``SNORE_MAX_UPLOAD_BYTES``
    Per-upload ingress byte ceiling, enforced in the ASGI receive stream before
    any parser spooling begins.  Default: 512 MiB.  Accepts integer bytes.

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


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration."""

    auth_mode: AuthMode
    session_secret: str  # Empty string in local mode; required in multiuser.
    public_base_url: str  # Validated URL or empty string in local mode.
    bind_host: str
    trusted_proxies: frozenset[str]
    # Upload / job resource bounds
    max_upload_bytes: int  # Per-upload ingress ceiling (bytes); default 512 MiB.
    max_jobs_per_user: int  # Per-user active-job cap; default 3.
    max_jobs_global: int  # Global active-job cap; default 10.

    @property
    def is_multiuser(self) -> bool:
        return self.auth_mode is AuthMode.MULTIUSER

    @property
    def secure_cookie(self) -> bool:
        """True when the public base URL is HTTPS (non-loopback).

        ``Secure`` is off only when the validated public base URL is a
        loopback HTTP URL — i.e. ``just dev-auth`` over plain local HTTP.
        Any non-loopback public URL must be HTTPS and forces ``Secure``.
        """
        if not self.public_base_url:
            return False
        parsed = urlparse(self.public_base_url)
        if parsed.scheme == "https":
            return True
        # HTTP is allowed only for loopback.
        host = parsed.hostname or ""
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            # Non-numeric host (e.g. "localhost") — treat as loopback-safe.
            return host not in ("localhost", "localhost.localdomain")

    @property
    def cookie_domain(self) -> str | None:
        """Domain for the session cookie, or None to let the browser use the request host."""
        if not self.public_base_url:
            return None
        host = urlparse(self.public_base_url).hostname
        return host if host and "." in host else None


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
        bind_host=bind_host,
        trusted_proxies=trusted_proxies,
        max_upload_bytes=max_upload_bytes,
        max_jobs_per_user=max_jobs_per_user,
        max_jobs_global=max_jobs_global,
    )


def _validate_public_base_url(url: str) -> None:
    """Raise ConfigError if ``url`` is not a valid loopback HTTP or HTTPS URL."""
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
