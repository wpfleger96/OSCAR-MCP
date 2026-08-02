from __future__ import annotations

import importlib.resources
import logging
import os

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from snore.api.errors import NotFoundError, not_found_handler, server_error_handler
from snore.api.import_jobs import shutdown as _shutdown_import_jobs
from snore.api.import_jobs import start_reaper as _start_import_reaper
from snore.api.middleware import AuthMiddleware, RateLimitMiddleware
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
from snore.database.session import init_database, init_database_from_url

API_V1_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Honour the canonical URL exported by `snore serve` first; fall back to
    # SNORE_DB_PATH for direct uvicorn invocations and the e2e test harness.
    database_url = os.environ.get("SNORE_DATABASE_URL")
    if database_url:
        await init_database_from_url(database_url)
    else:
        db_path = os.environ.get("SNORE_DB_PATH")
        await init_database(db_path)

    # Startup recovery + acquire lifetime shared writer lease.
    # 1. Acquire exclusive briefly for startup recovery (finish any interrupted
    #    deletion sagas from a previous run).
    # 2. Run recovery synchronously (it uses asyncio.run internally, but we're
    #    in the lifespan which has its own event loop — call it via to_thread).
    # 3. Release exclusive, then acquire shared for the process lifetime.
    import asyncio  # noqa: PLC0415

    from snore.services.profile_service import DeletionSaga  # noqa: PLC0415
    from snore.services.writer_lease import get_writer_lease  # noqa: PLC0415

    lease = get_writer_lease()

    # Try exclusive for startup recovery.  If we can't get it (another process
    # is somehow running), log a warning and continue without recovery —
    # the tombstone will be found again at the next restart.
    try:
        lease.acquire_exclusive()
        saga = DeletionSaga()
        try:
            await asyncio.to_thread(saga.recover)
        finally:
            # Downgrade to shared hold for process lifetime.
            # Release exclusive and immediately acquire shared.
            lease.release_exclusive()
            lease.acquire_shared()
    except Exception as exc:
        # Failed to acquire exclusive (another process) or recovery failed.
        # Acquire shared anyway — we serve read-write, just without recovery.
        logger.warning("Startup recovery skipped: %s", exc)
        lease.acquire_shared()

    # Start a single lifespan-owned TTL reaper.
    reaper_thread, reaper_stop = _start_import_reaper(interval=60.0)
    try:
        yield
    finally:
        # Stop the reaper first.
        reaper_stop.set()
        reaper_thread.join(timeout=5.0)
        # Cancel all in-flight import jobs and await their threads.
        still_alive = _shutdown_import_jobs()
        if still_alive:
            raise RuntimeError(
                f"Shutdown incomplete: {len(still_alive)} import worker(s) still alive "
                f"after timeout: {still_alive}. Active import writes may be interrupted."
            )
        # Release the lifetime shared writer lease.
        lease.release()


def create_app() -> FastAPI:
    app = FastAPI(
        title="SNORE API",
        version=__version__,
        lifespan=lifespan,
    )

    cors_origins = [
        o.strip()
        for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    ]
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(Exception, server_error_handler)

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

    app.include_router(
        import_data.router, prefix=f"{API_V1_PREFIX}/import", tags=["import"]
    )
    app.include_router(
        reports.router, prefix=f"{API_V1_PREFIX}/reports", tags=["reports"]
    )
    app.include_router(export.router, prefix=f"{API_V1_PREFIX}/export", tags=["export"])
    app.include_router(db.router, prefix=f"{API_V1_PREFIX}/db", tags=["database"])
    app.include_router(
        validation.router, prefix=f"{API_V1_PREFIX}/validate", tags=["validation"]
    )
    app.include_router(
        profiles.router, prefix=f"{API_V1_PREFIX}/profiles", tags=["profiles"]
    )

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
