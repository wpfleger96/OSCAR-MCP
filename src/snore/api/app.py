from __future__ import annotations

import importlib.resources
import os

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from snore.api.errors import NotFoundError, not_found_handler, server_error_handler
from snore.api.middleware import AuthMiddleware, RateLimitMiddleware
from snore.api.routers import (
    analysis,
    days,
    db,
    devices,
    events,
    export,
    import_data,
    rx,
    sessions,
    stats,
    validation,
    waveforms,
)
from snore.database.session import init_database

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = os.environ.get("SNORE_DB_PATH")
    init_database(db_path)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="SNORE API",
        version="0.1.0",
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
    app.include_router(export.router, prefix=f"{API_V1_PREFIX}/export", tags=["export"])
    app.include_router(db.router, prefix=f"{API_V1_PREFIX}/db", tags=["database"])
    app.include_router(
        validation.router, prefix=f"{API_V1_PREFIX}/validate", tags=["validation"]
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
