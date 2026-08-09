from __future__ import annotations

import os
import sqlite3
import sys
import time

from fastapi import APIRouter
from pydantic import BaseModel


class AboutInfo(BaseModel):
    version: str
    git_sha: str
    build_time: str
    uptime_seconds: float
    auth_mode: str
    python_version: str
    sqlite_version: str


router = APIRouter()


@router.get("/about", include_in_schema=False)
async def get_about() -> AboutInfo:
    from snore.api.app import _STARTUP_TIME, __version__  # noqa: PLC0415
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    return AboutInfo(
        version=__version__,
        git_sha=os.environ.get("SNORE_GIT_SHA", "dev"),
        build_time=os.environ.get("SNORE_BUILD_TIME", ""),
        uptime_seconds=round(time.monotonic() - _STARTUP_TIME, 1),
        auth_mode="Multi-user" if cfg.is_multiuser else "Local (single-user)",
        python_version=sys.version,
        sqlite_version=sqlite3.sqlite_version,
    )
