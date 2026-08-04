"""
Register all available device parsers.

This module provides explicit parser registration, which is safer than
auto-registration at module import time. Call ``register_all_parsers()``
at application startup, or ``ensure_registered_parsers()`` in idempotent
contexts (e.g. ``BreathService.get_device_capabilities()`` and the
``docs://capabilities`` lifespan hook).
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snore.parsers.base import DeviceParser

logger = logging.getLogger(__name__)


def _build_parser_instances() -> list[DeviceParser]:
    """Return fresh parser instances for all known parsers (shared factory list)."""
    from snore.parsers.base import DeviceParser as _DeviceParser  # noqa: PLC0415

    parsers: list[_DeviceParser] = []
    try:
        from snore.parsers.resmed_edf import ResmedEDFParser  # noqa: PLC0415

        parsers.append(ResmedEDFParser())
    except Exception:
        pass
    try:
        from snore.parsers.oscar_device import OscarDeviceParser  # noqa: PLC0415

        parsers.append(OscarDeviceParser())
    except Exception:
        pass
    return parsers


def register_all_parsers() -> None:
    """
    Register all available device parsers with the global registry.

    This function attempts to import and register each parser individually,
    with proper error handling. If a parser fails to import or register,
    a warning is logged but other parsers continue to load.

    This should be called once at application startup.  For idempotent
    call sites (tools, lifespan hooks) prefer ``ensure_registered_parsers()``
    — it skips already-registered IDs without emitting false errors.
    """
    from snore.parsers.registry import parser_registry

    try:
        from snore.parsers.resmed_edf import ResmedEDFParser

        parser_registry.register(ResmedEDFParser())
        logger.debug("Registered ResMed EDF+ parser")
    except ImportError as e:
        logger.warning(f"ResMed EDF+ parser not available: {e}")
    except Exception as e:
        logger.error(f"Failed to register ResMed EDF+ parser: {e}", exc_info=True)

    try:
        from snore.parsers.oscar_device import OscarDeviceParser

        parser_registry.register(OscarDeviceParser())
        logger.debug("Registered OSCAR binary parser")
    except ImportError as e:
        logger.warning(f"OSCAR binary parser not available: {e}")
    except Exception as e:
        logger.error(f"Failed to register OSCAR binary parser: {e}", exc_info=True)

    registered_count = len(parser_registry.list_parsers())
    logger.debug(
        f"Parser registration complete: {registered_count} parser(s) available"
    )


def ensure_registered_parsers() -> None:
    """Register any parser IDs not yet in the global registry.

    Safe to call repeatedly: existing IDs are read via the public
    ``parser_registry.list_parsers()`` and already-registered parsers are
    skipped — no duplicate-ID errors, no false error logs.  Handles partially
    populated registries correctly.

    Call sites: ``BreathService.get_device_capabilities()`` and the
    ``docs://capabilities`` lifespan hook — both may run in cold-start
    processes where ``register_all_parsers()`` has not yet been called.
    """
    from snore.parsers.registry import parser_registry

    existing_ids = {p.parser_id for p in parser_registry.list_parsers()}
    for parser in _build_parser_instances():
        try:
            if parser.parser_id not in existing_ids:
                parser_registry.register(parser)
                existing_ids.add(parser.parser_id)
                logger.debug(f"Registered parser: {parser.parser_id}")
        except Exception:
            logger.warning(
                f"Parser registration skipped: {getattr(parser, 'parser_id', '?')}",
                exc_info=True,
            )
