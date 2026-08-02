"""MCP-specific exception types for SNORE."""

from __future__ import annotations


class AnalysisNotRunError(Exception):
    """Raised when a tool requires analysis results that have not been computed."""


class CapabilityUnavailableError(Exception):
    """Raised when the device/dataset does not provide a requested capability."""


class ResponseSizeLimitError(Exception):
    """Raised when the tool response would exceed the size limit."""


class ValidationError(Exception):
    """Raised when tool input validation fails."""
