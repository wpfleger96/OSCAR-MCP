"""
Shared OSCAR binary header parsing.

Both the .000 summary and .001 events files begin with the same 32-byte base
header; the events file additionally carries a 10-byte extended header on
version >= 10.  This module holds the single implementation both parsers wrap,
each supplying its own file-type check and exception class.
"""

import struct

from typing import Any

from snore.constants import OSCAR_MAGIC_NUMBER


def parse_oscar_header(
    stream: Any,
    *,
    expected_file_type: int,
    error_cls: type[Exception],
    read_extended: bool = False,
) -> dict[str, Any]:
    """Parse an OSCAR binary header from ``stream``.

    Reads the 32-byte base header (magic, version, file type, machine/session
    ids, first/last timestamps), validating the magic number and file type.
    When ``read_extended`` is set and the file version is >= 10, the 10-byte
    events extended header (compression, machine type, data size, crc16) is
    also read.

    Args:
        stream: Binary stream positioned at the start of the header.
        expected_file_type: Required file-type value (0 summary, 1 events).
        error_cls: Exception type raised on any header validation failure.
        read_extended: Read the version>=10 extended header (events only).

    Returns:
        Dictionary of header fields.  Extended keys are present only when
        ``read_extended`` is set.

    Raises:
        error_cls: If the header is too short, the magic number is wrong, or
            the file type does not match ``expected_file_type``.
    """
    base_header = stream.read(32)
    if len(base_header) != 32:
        raise error_cls("File too short to contain header")

    (
        magic,
        version,
        file_type,
        machine_id,
        session_id,
        first_timestamp,
        last_timestamp,
    ) = struct.unpack("<IHH II qq", base_header)

    if magic != OSCAR_MAGIC_NUMBER:
        raise error_cls(
            f"Invalid magic number: 0x{magic:08x} (expected 0x{OSCAR_MAGIC_NUMBER:08x})"
        )

    if file_type != expected_file_type:
        label = "summary" if expected_file_type == 0 else "events"
        raise error_cls(
            f"Invalid file type: {file_type} (expected {expected_file_type} for {label})"
        )

    header: dict[str, Any] = {
        "magic": magic,
        "version": version,
        "file_type": file_type,
        "machine_id": machine_id,
        "session_id": session_id,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
    }

    if read_extended:
        compression = 0
        machine_type = 0
        data_size = 0
        crc16 = 0
        if version >= 10:
            ext_header = stream.read(10)
            if len(ext_header) != 10:
                raise error_cls(f"File too short for version {version} extended header")
            (compression, machine_type, data_size, crc16) = struct.unpack(
                "<HH iH", ext_header
            )
        header.update(
            {
                "compression": compression,
                "machine_type": machine_type,
                "data_size": data_size,
                "crc16": crc16,
            }
        )

    return header
