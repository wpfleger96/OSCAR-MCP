from __future__ import annotations

import os
import sqlite3
import sys
import time

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from snore.constants import DEFAULT_DEPLOY_DEFERRED_MARKER

_MARKER_FRESHNESS = timedelta(
    minutes=30
)  # hook touches the marker every ~300s poll while deferring


class AboutInfo(BaseModel):
    version: str
    git_sha: str
    build_time: str
    uptime_seconds: float
    auth_mode: str
    python_version: str
    sqlite_version: str
    update_pending: bool
    update_deferred_since: str | None


router = APIRouter()


def _read_deploy_deferred(marker: Path) -> tuple[bool, str | None]:
    try:
        mtime = datetime.fromtimestamp(marker.stat().st_mtime, tz=UTC)
    except OSError:
        return False, None
    if datetime.now(tz=UTC) - mtime > _MARKER_FRESHNESS:
        return False, None
    try:
        since = datetime.fromisoformat(marker.read_text().strip()).isoformat()
    except (OSError, ValueError):
        since = (
            mtime.isoformat()
        )  # content is best-effort metadata; existence is the signal
    return True, since


@router.get("/about", include_in_schema=False)
async def get_about() -> AboutInfo:
    from snore.api.app import _STARTUP_TIME, __version__  # noqa: PLC0415
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    update_pending, update_deferred_since = _read_deploy_deferred(
        DEFAULT_DEPLOY_DEFERRED_MARKER
    )
    return AboutInfo(
        version=__version__,
        git_sha=os.environ.get("SNORE_GIT_SHA", "dev"),
        build_time=os.environ.get("SNORE_BUILD_TIME", ""),
        uptime_seconds=round(time.monotonic() - _STARTUP_TIME, 1),
        auth_mode="Multi-user" if cfg.is_multiuser else "Local (single-user)",
        python_version=sys.version,
        sqlite_version=sqlite3.sqlite_version,
        update_pending=update_pending,
        update_deferred_since=update_deferred_since,
    )
