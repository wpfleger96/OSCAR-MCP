"""Tests for the shared OSCAR binary header parser.

No ``.000``/``.001`` fixtures exist and the device tests patch
``parse_summary_file``/``parse_events_file``, so ``parse_oscar_header`` is
otherwise never executed under test.  These build the 32-byte base header (and
the version>=10 10-byte extension) from ``struct.pack`` over in-memory streams
and exercise the happy paths plus every validation failure, for both parser
exception classes.
"""

from __future__ import annotations

import io
import struct

import pytest

from snore.constants import OSCAR_MAGIC_NUMBER
from snore.parsers.oscar_events import OscarEventsParseError
from snore.parsers.oscar_header import parse_oscar_header
from snore.parsers.oscar_summary import OscarSummaryParseError

pytestmark = pytest.mark.unit


def _base_header(
    *,
    magic: int = OSCAR_MAGIC_NUMBER,
    version: int = 18,
    file_type: int = 0,
    machine_id: int = 7,
    session_id: int = 12345,
    first_ts: int = 1_000,
    last_ts: int = 2_000,
) -> bytes:
    return struct.pack(
        "<IHH II qq",
        magic,
        version,
        file_type,
        machine_id,
        session_id,
        first_ts,
        last_ts,
    )


def _ext_header(
    *,
    compression: int = 1,
    machine_type: int = 2,
    data_size: int = 4096,
    crc16: int = 999,
) -> bytes:
    return struct.pack("<HH iH", compression, machine_type, data_size, crc16)


class TestValidHeaders:
    def test_summary_header_file_type_0(self):
        stream = io.BytesIO(_base_header(file_type=0, version=18))
        header = parse_oscar_header(
            stream, expected_file_type=0, error_cls=OscarSummaryParseError
        )
        assert header == {
            "magic": OSCAR_MAGIC_NUMBER,
            "version": 18,
            "file_type": 0,
            "machine_id": 7,
            "session_id": 12345,
            "first_timestamp": 1_000,
            "last_timestamp": 2_000,
        }
        # No extended fields without read_extended.
        assert "compression" not in header

    def test_events_header_version_below_10_has_default_extended_fields(self):
        stream = io.BytesIO(_base_header(file_type=1, version=9))
        header = parse_oscar_header(
            stream,
            expected_file_type=1,
            error_cls=OscarEventsParseError,
            read_extended=True,
        )
        # version < 10: extension not read, defaults reported.
        assert header["version"] == 9
        assert header["compression"] == 0
        assert header["machine_type"] == 0
        assert header["data_size"] == 0
        assert header["crc16"] == 0

    def test_events_header_version_10_reads_extended(self):
        stream = io.BytesIO(
            _base_header(file_type=1, version=10)
            + _ext_header(compression=1, machine_type=2, data_size=4096, crc16=999)
        )
        header = parse_oscar_header(
            stream,
            expected_file_type=1,
            error_cls=OscarEventsParseError,
            read_extended=True,
        )
        assert header["version"] == 10
        assert header["compression"] == 1
        assert header["machine_type"] == 2
        assert header["data_size"] == 4096
        assert header["crc16"] == 999


class TestErrorPaths:
    def test_short_base_header_raises(self):
        stream = io.BytesIO(b"\x00" * 16)
        with pytest.raises(OscarSummaryParseError, match="too short to contain header"):
            parse_oscar_header(
                stream, expected_file_type=0, error_cls=OscarSummaryParseError
            )

    def test_short_extended_header_raises(self):
        stream = io.BytesIO(_base_header(file_type=1, version=10) + b"\x00" * 4)
        with pytest.raises(OscarEventsParseError, match="extended header"):
            parse_oscar_header(
                stream,
                expected_file_type=1,
                error_cls=OscarEventsParseError,
                read_extended=True,
            )

    def test_bad_magic_raises(self):
        stream = io.BytesIO(_base_header(magic=0xDEADBEEF, file_type=0))
        with pytest.raises(OscarSummaryParseError, match="Invalid magic number"):
            parse_oscar_header(
                stream, expected_file_type=0, error_cls=OscarSummaryParseError
            )

    def test_wrong_file_type_summary_raises(self):
        stream = io.BytesIO(_base_header(file_type=1))
        with pytest.raises(OscarSummaryParseError, match="expected 0 for summary"):
            parse_oscar_header(
                stream, expected_file_type=0, error_cls=OscarSummaryParseError
            )

    def test_wrong_file_type_events_raises(self):
        stream = io.BytesIO(_base_header(file_type=0))
        with pytest.raises(OscarEventsParseError, match="expected 1 for events"):
            parse_oscar_header(
                stream,
                expected_file_type=1,
                error_cls=OscarEventsParseError,
                read_extended=True,
            )
