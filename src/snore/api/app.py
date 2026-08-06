from __future__ import annotations

import importlib.resources
import logging
import os
import resource

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from snore.api.analysis_jobs import shutdown as _shutdown_analysis_jobs
from snore.api.analysis_jobs import start_worker as _start_analysis_worker
from snore.api.errors import (
    NotFoundError,
    auth_validation_error_handler,
    not_found_handler,
    server_error_handler,
)
from snore.api.import_jobs import shutdown as _shutdown_import_jobs
from snore.api.import_jobs import start_reaper as _start_import_reaper
from snore.api.middleware import AuthMiddleware, AuthPathMiddleware, RateLimitMiddleware
from snore.api.routers import (
    analysis,
    days,
    db,
    devices,
    events,
    export,
    import_data,
    profiles,
    reports,
    rx,
    sessions,
    stats,
    validation,
    waveforms,
)
from snore.api.routers import auth as auth_router
from snore.database.session import init_database, init_database_from_url

API_V1_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Raise the FD soft limit so Starlette's multipart parser can open a
    # SpooledTemporaryFile per uploaded file without hitting EMFILE.
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _target = min(_hard, 65_536) if _hard != resource.RLIM_INFINITY else 65_536
    if _soft < _target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
        logger.info("Raised file-descriptor limit: %d → %d", _soft, _target)

    # Load and validate config first — fail fast on misconfiguration.
    from snore.api.config import load_config, set_config  # noqa: PLC0415

    bind_host = os.environ.get("SNORE_BIND_HOST", "127.0.0.1")
    cfg = load_config(bind_host_override=bind_host)
    set_config(cfg)

    if cfg.is_multiuser and not cfg.is_google_configured:
        logger.warning(
            "Google OAuth not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET absent)"
            " — /auth/google endpoints will return 503"
        )

    # Honour the canonical URL exported by `snore serve` first; fall back to
    # SNORE_DB_PATH for direct uvicorn invocations and the e2e test harness.
    database_url = os.environ.get("SNORE_DATABASE_URL")
    if database_url:
        await init_database_from_url(database_url)
    else:
        db_path = os.environ.get("SNORE_DB_PATH")
        await init_database(db_path)

    # Startup recovery + acquire lifetime shared writer lease.
    import asyncio  # noqa: PLC0415

    from snore.services.profile_service import DeletionSaga  # noqa: PLC0415
    from snore.services.writer_lease import get_writer_lease  # noqa: PLC0415

    lease = get_writer_lease()

    exclusive_held = False
    try:
        lease.acquire_exclusive()
        exclusive_held = True
    except Exception as exc:
        logger.warning(
            "Startup recovery skipped (exclusive lease unavailable): %s", exc
        )

    if exclusive_held:
        saga = DeletionSaga()
        try:
            await asyncio.to_thread(saga.recover)
        except Exception as exc:
            logger.warning("Startup recovery failed: %s", exc)
        finally:
            lease.release_exclusive()

    lease.acquire_shared()

    # Purge expired/consumed oauth_attempts at startup.
    await _startup_purge_expired_oauth_attempts()

    # Clean orphaned import-spool temp directories left by a crashed process.
    _cleanup_stale_upload_tempdirs()

    # Start a single lifespan-owned TTL reaper and the analysis job worker.
    reaper_thread, reaper_stop = _start_import_reaper(interval=60.0)
    _start_analysis_worker()
    try:
        yield
    finally:
        reaper_stop.set()
        reaper_thread.join(timeout=5.0)
        still_alive = _shutdown_import_jobs()
        if still_alive:
            raise RuntimeError(
                f"Shutdown incomplete: {len(still_alive)} import worker(s) still alive "
                f"after timeout: {still_alive}. Active import writes may be interrupted."
            )
        _shutdown_analysis_jobs()
        lease.release()


async def _startup_purge_expired_oauth_attempts() -> None:
    """Delete expired and consumed oauth_attempts rows at startup."""
    try:
        from datetime import UTC, datetime  # noqa: PLC0415

        from snore.database.session import session_scope  # noqa: PLC0415

        now = datetime.now(UTC)
        async with session_scope() as db:
            purged = await auth_router._purge_expired_oauth_attempts(db, now)
        if purged:
            logger.info("Purged %d expired/consumed oauth_attempts rows", purged)
    except Exception as exc:
        logger.warning("oauth_attempts purge failed: %s", exc)


# Stale-temp retention: any snore-upload-* dir older than this is orphaned.
_STALE_UPLOAD_TMPDIR_AGE_SECONDS: float = 2 * 3600  # 2 hours


def _cleanup_stale_upload_tempdirs() -> None:
    """Remove orphaned ``snore-upload-*`` temp dirs from a previous crashed process.

    A normal upload cleans its temp directory via ``ImportJob.cleanup_files()``
    on every terminal path.  A hard crash (SIGKILL, OOM) leaks the spool tree
    forever.  This scans ``tempfile.gettempdir()`` at startup and removes any
    ``snore-upload-*`` directories that are older than
    ``_STALE_UPLOAD_TMPDIR_AGE_SECONDS``.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import time  # noqa: PLC0415

    from pathlib import Path  # noqa: PLC0415

    tmpdir = Path(tempfile.gettempdir())
    now = time.time()
    cleaned = 0
    for entry in tmpdir.iterdir():
        if not entry.name.startswith("snore-upload-"):
            continue
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
            if age > _STALE_UPLOAD_TMPDIR_AGE_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("Cleaned stale upload temp dir: %s (age=%.0fs)", entry, age)
                cleaned += 1
        except OSError as exc:
            logger.warning("Could not check/remove stale temp dir %s: %s", entry, exc)
    if cleaned:
        logger.info("Startup: removed %d stale upload temp dir(s)", cleaned)


def create_app() -> FastAPI:
    from snore.api.config import get_config  # noqa: PLC0415

    # Ensure config is loaded.  In tests, callers set it via set_config() before
    # calling create_app(); in production, lifespan sets it.  get_config() lazily
    # loads from env when the global config is not yet set.
    cfg = get_config()

    is_multiuser = cfg.is_multiuser

    app = FastAPI(
        title="SNORE API",
        version=__version__,
        lifespan=lifespan,
    )

    # Middleware add order is innermost-first; requests are processed outermost-first.
    # AuthPathMiddleware must be innermost of the auth pair so RateLimitMiddleware
    # fires before the body pre-read, rejecting rate-limited requests cheaply.
    app.add_middleware(AuthPathMiddleware)  # innermost of auth pair
    app.add_middleware(RateLimitMiddleware)  # outermost of auth pair
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(Exception, server_error_handler)
    # Strip credential inputs from 422 validation errors on auth routes.
    from fastapi.exceptions import RequestValidationError  # noqa: PLC0415

    app.add_exception_handler(RequestValidationError, auth_validation_error_handler)

    # Auth router — always registered (public endpoints + mode-aware).
    app.include_router(
        auth_router.router, prefix=f"{API_V1_PREFIX}/auth", tags=["auth"]
    )

    app.include_router(
        devices.router, prefix=f"{API_V1_PREFIX}/devices", tags=["devices"]
    )
    app.include_router(
        sessions.router, prefix=f"{API_V1_PREFIX}/sessions", tags=["sessions"]
    )
    app.include_router(stats.router, prefix=f"{API_V1_PREFIX}/stats", tags=["stats"])

    app.include_router(
        waveforms.router, prefix=f"{API_V1_PREFIX}/sessions", tags=["waveforms"]
    )
    app.include_router(
        events.router, prefix=f"{API_V1_PREFIX}/sessions", tags=["events"]
    )

    app.include_router(analysis.router, prefix=API_V1_PREFIX, tags=["analysis"])
    app.include_router(days.router, prefix=f"{API_V1_PREFIX}/days", tags=["days"])
    app.include_router(rx.router, prefix=f"{API_V1_PREFIX}/rx", tags=["rx"])

    # /import/detect and /import/path are local-mode-only (server-path import).
    # In multiuser mode these routes are NOT registered — the loopback-peer
    # check is worthless behind Cloudflare; uploads-only is the contract.
    app.include_router(
        import_data.router,
        prefix=f"{API_V1_PREFIX}/import",
        tags=["import"],
    )
    if not is_multiuser:
        app.include_router(
            import_data.local_only_router,
            prefix=f"{API_V1_PREFIX}/import",
            tags=["import"],
        )

    app.include_router(
        reports.router, prefix=f"{API_V1_PREFIX}/reports", tags=["reports"]
    )
    app.include_router(export.router, prefix=f"{API_V1_PREFIX}/export", tags=["export"])

    # /db router: /db/reset is removed from the web API in multiuser mode.
    # In multiuser, register a restricted db router that excludes /reset.
    app.include_router(db.router, prefix=f"{API_V1_PREFIX}/db", tags=["database"])
    if not is_multiuser:
        app.include_router(
            db.local_only_router,
            prefix=f"{API_V1_PREFIX}/db",
            tags=["database"],
        )

    app.include_router(
        validation.router, prefix=f"{API_V1_PREFIX}/validate", tags=["validation"]
    )
    app.include_router(
        profiles.router, prefix=f"{API_V1_PREFIX}/profiles", tags=["profiles"]
    )

    # Excluded from the OpenAPI schema deliberately — keeps the health probe
    # out of generated API clients and avoids ui/src/types/generated.ts churn.
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_spa(app)

    return app


def _resolve_spa_dist() -> Path | None:
    dist = Path(str(importlib.resources.files("snore"))) / "ui" / "dist"
    if dist.is_dir():
        return dist
    project_dist = Path(__file__).resolve().parents[3] / "ui" / "dist"
    if project_dist.is_dir():
        return project_dist
    return None


def _mount_spa(app: FastAPI) -> None:
    dist = _resolve_spa_dist()
    if dist is None:
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

    @app.middleware("http")
    async def spa_fallback(request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[operator]
        if response.status_code == 404 and not request.url.path.startswith("/api/"):
            return FileResponse(dist / "index.html")
        return response
