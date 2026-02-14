"""Compression and decompression utilities for OSCAR data."""

import struct
import zlib


class QtCompressionError(Exception):
    """Exception raised when Qt compression/decompression fails."""

    pass


def qUncompress(data: bytes) -> bytes:
    """
    Decompress data compressed with Qt's qCompress().

    Qt's qCompress format:
    - 4 bytes: Uncompressed data size (big-endian uint32)
    - N bytes: zlib-compressed data

    Args:
        data: Compressed data bytes

    Returns:
        Decompressed data bytes

    Raises:
        QtCompressionError: If decompression fails
    """
    if len(data) < 4:
        raise QtCompressionError("Data too short for Qt compressed format")

    uncompressed_size = struct.unpack(">I", data[:4])[0]

    compressed_data = data[4:]

    try:
        decompressed = zlib.decompress(compressed_data)
    except zlib.error as e:
        raise QtCompressionError(f"zlib decompression failed: {e}") from e

    if len(decompressed) != uncompressed_size:
        raise QtCompressionError(
            f"Decompressed size mismatch: expected {uncompressed_size}, got {len(decompressed)}"
        )

    return decompressed
