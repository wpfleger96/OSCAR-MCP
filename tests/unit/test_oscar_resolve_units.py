"""Regression tests for OSCAR `_resolve_units` root handling.

The unified parse driver resolves a single data root per call.  These pin the
empty-roots guard (no IndexError, no units) and the multi-root warning path.
"""

from __future__ import annotations

import logging

from pathlib import Path
from unittest.mock import patch

import pytest

from snore.parsers.base import ParserDetectionResult
from snore.parsers.oscar_device import OscarDeviceParser
from snore.parsers.types import DataRoot

pytestmark = pytest.mark.unit


def _root(path: str) -> DataRoot:
    return DataRoot(
        path=Path(path),
        structure_type="oscar_profile",
        profile_name="p",
        device_serial="s",
        confidence=0.95,
    )


def test_empty_roots_yields_no_units_without_indexerror():
    """A detected-but-empty root list must return no units, never IndexError."""
    parser = OscarDeviceParser()
    parser._data_roots = []  # falsy → detect() path, which we stub as empty

    with patch.object(
        parser, "detect", return_value=ParserDetectionResult(detected=True)
    ):
        root, units = parser._resolve_units(Path("/data"), None, None)

    assert units == []
    assert root == Path("/data")


def test_empty_roots_full_parse_yields_nothing():
    parser = OscarDeviceParser()
    parser._data_roots = []

    with patch.object(
        parser, "detect", return_value=ParserDetectionResult(detected=True)
    ):
        assert list(parser.parse_sessions(Path("/data"))) == []


def test_multi_root_warns_and_parses_first(caplog):
    """A path resolving to several roots parses the first and logs the rest."""
    parser = OscarDeviceParser()
    parser._data_roots = [_root("/data/a"), _root("/data/b"), _root("/data/c")]

    # Path matches no root → detect() branch → roots_to_parse = _data_roots.
    with (
        patch.object(
            parser, "detect", return_value=ParserDetectionResult(detected=True)
        ),
        caplog.at_level(logging.WARNING, logger="snore.parsers.oscar_device"),
    ):
        root, units = parser._resolve_units(Path("/data"), None, None)

    assert root == Path("/data/a")
    assert units == []  # nonexistent Summaries/Events dirs → no session files
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "3 data roots" in m and "/data/b" in m and "/data/c" in m for m in warnings
    )
