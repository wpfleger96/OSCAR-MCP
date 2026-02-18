from __future__ import annotations

import os

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from snore.api.errors import NotFoundError, not_found_handler, server_error_handler
from snore.api.middleware import AuthMiddleware, RateLimitMiddleware
from snore.api.routers import (
    analysis,
    days,
    devices,
    events,
    rx,
    sessions,
    stats,
    waveforms,
)
from snore.database.session import init_database

API_V1_PREFIX = "/api/v1"

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = os.environ.get("SNORE_DB_PATH")
    init_database(db_path)
    yield


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

    return app
