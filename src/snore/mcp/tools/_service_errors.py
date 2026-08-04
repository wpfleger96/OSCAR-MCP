"""Shared mapping of BreathService exceptions to MCP ValidationError."""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy.exc import OperationalError

from snore.mcp.errors import ValidationError
from snore.services.breath_service import (
    DeviceAmbiguityError,
    DeviceNotOwnedError,
    MultiSessionAmbiguityError,
)

MAPPED_SERVICE_ERRORS = (
    DeviceNotOwnedError,
    DeviceAmbiguityError,
    MultiSessionAmbiguityError,
    OperationalError,
)


def raise_mapped_service_error(exc: Exception) -> NoReturn:
    """Convert a known BreathService exception to ValidationError.

    Message formats are frozen (§3 of the Stage-2 interface contract).
    NEVER include the profile_id in user-facing messages — it is server-internal
    and leaking it aids enumeration.

    Raises:
        ValidationError: for DeviceNotOwnedError, DeviceAmbiguityError,
            MultiSessionAmbiguityError, or OperationalError("no such table").
        The original exception: for any other OperationalError or unexpected type.
    """
    if isinstance(exc, DeviceNotOwnedError):
        raise ValidationError(
            f"device_id={exc.device_id} is not available in this session"
        ) from exc

    if isinstance(exc, DeviceAmbiguityError):
        device_list = ", ".join(
            f"device_id={d} (serial={exc.device_serials.get(d, '')})"
            for d in exc.owned_device_ids
        )
        raise ValidationError(
            f"Multiple devices have sessions on {exc.therapy_date}: "
            f"pass device_id to disambiguate. Devices: {device_list}"
        ) from exc

    if isinstance(exc, MultiSessionAmbiguityError):
        session_list = "; ".join(
            f"session_id={s.session_id} start={s.start_wall_clock.isoformat()} "
            f"duration_s={round(s.duration_seconds)}"
            for s in exc.sessions
        )
        raise ValidationError(
            f"Multiple sessions on {exc.therapy_date} for device_id={exc.device_id}: "
            f"pass session_id to disambiguate. Sessions: {session_list}"
        ) from exc

    if isinstance(exc, OperationalError) and "no such table" in str(exc):
        raise ValidationError(
            "Breath-level data tables are missing from this database "
            "(reason: table_missing). Run 'snore analysis run' or re-import "
            "with analysis enabled to generate breath data."
        ) from exc

    # Any other OperationalError or unknown type — propagate unchanged.
    raise exc
