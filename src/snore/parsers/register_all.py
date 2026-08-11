"""
Register all available device parsers.

This module provides explicit parser registration, which is safer than
auto-registration at module import time. Call ``ensure_registered_parsers()``
at any call site — it is safe to invoke repeatedly and will not raise if
parsers are already registered.
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


def ensure_registered_parsers() -> None:
    """Register any parser IDs not yet in the global registry.

    Safe to call repeatedly: existing IDs are read via the public
    ``parser_registry.list_parsers()`` and already-registered parsers are
    skipped — no duplicate-ID errors, no false error logs.  Handles partially
    populated registries correctly.

    Call sites: ``BreathService.get_device_capabilities()``,
    ``ImportService.detect_sources()``, ``ExportService.export_raw()``, and the
    ``docs://capabilities`` lifespan hook.
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
