"""Domain exceptions for SNORE."""

from __future__ import annotations

__all__ = ["NotFoundError"]


class NotFoundError(ValueError):
    """Raised when a requested resource does not exist."""
