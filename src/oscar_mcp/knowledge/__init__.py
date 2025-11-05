"""
OSCAR-MCP Medical Knowledge Module

Simplified knowledge management with Python constants for:
- Flow limitation classes and respiratory event definitions
- Clinical thresholds and severity classifications
- Pattern relationships and complex breathing patterns
- OSCAR chart examples and reference images

Knowledge is stored as version-controlled Python constants instead of database entries,
enabling fast access, easy iteration, and clear version history.
"""

from . import patterns
from . import thresholds
from . import chart_examples

__all__ = [
    "patterns",
    "thresholds",
    "chart_examples",
]
