"""Shared helpers for MCP tool adapters."""

from __future__ import annotations


def _str_or_none(value: object) -> str | None:
    """Convert value to str, or return None if value is None."""
    return str(value) if value is not None else None
