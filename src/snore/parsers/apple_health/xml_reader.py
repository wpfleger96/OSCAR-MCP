"""Streaming export.xml reader for Apple Health data.

Handles zip archives (member ``apple_health_export/export.xml``) and directory
inputs.  Uses ``iterparse`` so that even 1.5 GB exports consume only a few MB
of memory.

``_DTDStripper`` pre-processes the byte stream to blank the malformed
``<!DOCTYPE ...>`` declaration shipped in iOS 16.0 exports, which would break
DTD-processing parsers.  stdlib ElementTree ignores DTDs but the stripper
provides defence-in-depth for future parser changes.
"""

from __future__ import annotations

import zipfile

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import IO
from xml.etree.ElementTree import iterparse

from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.type_handlers import parse_xml_record

# 10 GiB ceiling on decompressed XML bytes: a zip file well within the 2 GiB
# compressed upload cap can expand to an arbitrarily large XML stream (zip bomb),
# monopolising the serial import worker for hours.  Raise early if exceeded.
MAX_DECOMPRESSED_BYTES = 10 * 1024**3


class _DTDStripper:
    """Blanks any ``<!DOCTYPE...>`` region in the leading bytes of a binary stream.

    Byte positions are preserved — spaces replace the DOCTYPE token verbatim —
    so downstream parsers see the same offsets they expect.
    """

    # 64 KB covers any Apple Health Export DOCTYPE (typically ~3–5 KB).
    _SCAN_SIZE = 65_536

    def __init__(self, source: IO[bytes], max_bytes: int | None = None) -> None:
        head = bytearray(source.read(self._SCAN_SIZE))
        s = head.find(b"<!DOCTYPE")
        if s != -1:
            depth, i = 0, s
            while i < len(head):
                c = head[i]
                depth += (c == ord("[")) - (c == ord("]"))
                if c == ord(">") and depth == 0:
                    head[s : i + 1] = b" " * (i - s + 1)
                    break
                i += 1
            # Bound: if DOCTYPE's closing '>' falls beyond the 64 KB scan window
            # it is left intact — harmless because stdlib ElementTree ignores DTDs,
            # but a latent trap if the parser backend ever changes.
        self._buf = bytes(head)
        self._pos = 0
        self._tail: IO[bytes] = source
        self._bytes_consumed = 0
        self._max_bytes = max_bytes

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            rem = self._buf[self._pos :]
            self._pos = len(self._buf)
            chunk = rem + self._tail.read()
        else:
            avail = self._buf[self._pos : self._pos + n]
            self._pos += len(avail)
            if len(avail) < n:
                avail += self._tail.read(n - len(avail))
            chunk = avail
        self._bytes_consumed += len(chunk)
        if self._max_bytes is not None and self._bytes_consumed > self._max_bytes:
            raise ValueError(
                f"Decompressed XML stream exceeds {self._max_bytes:,} bytes "
                "(potential zip-bomb payload; import rejected)"
            )
        return chunk


def _open_xml_stream(source: Path) -> tuple[IO[bytes], Callable[[], None]]:
    """Return ``(stream, cleanup)`` for the export.xml inside *source*.

    Accepts a zip file, a directory (export.xml directly or under
    ``apple_health_export/``), or a bare file path.  ``cleanup`` closes all
    opened handles when called.
    """
    if source.is_dir():
        for candidate in (
            source / "export.xml",
            source / "apple_health_export" / "export.xml",
        ):
            if candidate.exists():
                f = open(candidate, "rb")  # noqa: SIM115
                return f, f.close
        raise FileNotFoundError(f"No export.xml found in directory {source}")

    # Try zip first; fall back to treating the path as a raw XML file.
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        f = open(source, "rb")  # noqa: SIM115
        return f, f.close

    members = [m for m in zf.namelist() if m.endswith("export.xml")]
    if not members:
        zf.close()
        raise FileNotFoundError(f"No export.xml member found in {source}")
    # Prefer the shortest path (e.g. apple_health_export/export.xml).
    member = min(members, key=len)
    stream = zf.open(member)

    def _cleanup() -> None:
        stream.close()
        zf.close()

    return stream, _cleanup


def iter_records(
    source: Path,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
    skip_counter: dict[str, int] | None = None,
) -> Iterator[RawHealthRecord]:
    """Stream ``RawHealthRecord`` objects from an Apple Health export.

    Args:
        source: Path to the ``export.zip``, the ``export.xml`` file, or a
            directory containing the export.
        date_from: If set, only yield records whose ``night_date >= date_from``.
        date_to: If set, only yield records whose ``night_date <= date_to``.
        limit: Maximum number of records to yield after date filtering.
        skip_counter: Mutable dict incremented per skipped HK type identifier.
            Pass an empty ``{}`` to collect skip reasons.

    Note:
        Records in export.xml are NOT sorted chronologically; date filtering
        requires scanning the whole file.
    """
    raw_stream, cleanup = _open_xml_stream(source)
    try:
        stream = _DTDStripper(raw_stream, max_bytes=MAX_DECOMPRESSED_BYTES)
        yielded = 0
        for _event, elem in iterparse(stream, events=("end",)):
            if elem.tag != "Record":
                elem.clear()
                continue

            hk_type = elem.get("type", "")
            record = parse_xml_record(elem)

            if record is None:
                if skip_counter is not None:
                    skip_counter[hk_type] = skip_counter.get(hk_type, 0) + 1
                elem.clear()
                continue

            elem.clear()

            if date_from is not None and record.night_date < date_from:
                continue
            if date_to is not None and record.night_date > date_to:
                continue

            yield record
            yielded += 1
            if limit is not None and yielded >= limit:
                return
    finally:
        cleanup()
