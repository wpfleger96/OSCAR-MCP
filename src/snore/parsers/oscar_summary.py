"""
OSCAR Summary File Parser

Parses .000 files containing session summary data and statistics.
Format version 18 (current OSCAR version).
"""

import struct

from pathlib import Path
from typing import Any

from snore.constants import OSCAR_MAGIC_NUMBER
from snore.parsers.qdatastream import QDataStreamReader
from snore.parsers.types import SessionSummary


class OscarSummaryParseError(Exception):
    """Exception raised when parsing OSCAR summary file fails."""

    pass


class OscarSummaryParser:
    """
    Parser for OSCAR .000 summary files.

    Reads session summary data including statistics for all channels.
    """

    def __init__(self, file_path: Path):
        """
        Initialize parser.

        Args:
            file_path: Path to .000 file to parse
        """
        self.file_path = Path(file_path)

    def parse(self) -> SessionSummary:
        """
        Parse the summary file.

        Returns:
            SessionSummary object with all session data

        Raises:
            OscarSummaryParseError: If parsing fails
            FileNotFoundError: If file doesn't exist
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"Summary file not found: {self.file_path}")

        try:
            with open(self.file_path, "rb") as f:
                return self._parse_stream(f)
        except Exception as e:
            raise OscarSummaryParseError(
                f"Failed to parse {self.file_path}: {e}"
            ) from e

    def _parse_stream(self, stream: Any) -> SessionSummary:
        """Parse summary file from binary stream."""
        header = self._parse_header(stream)

        summary = SessionSummary(
            magic=header["magic"],
            version=header["version"],
            file_type=header["file_type"],
            machine_id=header["machine_id"],
            session_id=header["session_id"],
            first_timestamp=header["first_timestamp"],
            last_timestamp=header["last_timestamp"],
        )

        reader = QDataStreamReader(stream)

        summary.settings = self._parse_settings(reader)

        try:
            if summary.version >= 18:
                summary.counts = reader.read_qhash_uint32_float()
                summary.sums = reader.read_qhash_uint32_double()
                summary.averages = reader.read_qhash_uint32_float()
                summary.weighted_averages = reader.read_qhash_uint32_float()
                summary.minimums = reader.read_qhash_uint32_float()
                summary.maximums = reader.read_qhash_uint32_float()
                summary.physical_minimums = reader.read_qhash_uint32_float()
                summary.physical_maximums = reader.read_qhash_uint32_float()
                summary.counts_per_hour = reader.read_qhash_uint32_float()
                summary.sums_per_hour = reader.read_qhash_uint32_float()
                summary.first_channel_time = reader.read_qhash_uint32_uint64()
                summary.last_channel_time = reader.read_qhash_uint32_uint64()
                summary.value_summaries = reader.read_qhash_nested()
                summary.time_summaries = reader.read_qhash_nested_time()
                summary.gains = reader.read_qhash_uint32_float()
                summary.available_channels = reader.read_qlist_uint32()

                summary.time_above_threshold = reader.read_qhash_uint32_uint64()
                summary.upper_threshold = reader.read_qhash_uint32_float()
                summary.time_below_threshold = reader.read_qhash_uint32_uint64()
                summary.lower_threshold = reader.read_qhash_uint32_float()

                summary.summary_only = reader.read_bool()
                summary.no_settings = reader.read_bool()

            else:
                raise OscarSummaryParseError(
                    f"Unsupported summary version: {summary.version}"
                )

        except EOFError as e:
            raise OscarSummaryParseError(f"Unexpected end of file: {e}") from e
        except Exception as e:
            raise OscarSummaryParseError(f"Error parsing session data: {e}") from e

        return summary

    def _parse_settings(self, reader: QDataStreamReader) -> dict[int, Any]:
        """
        Parse settings QHash<ChannelID, QVariant> from summary file.

        Returns:
            Dictionary mapping channel IDs to setting values
        """
        count = reader.read_uint32()
        settings = {}

        for _ in range(count):
            key = reader.read_uint32()
            value = reader.read_qvariant()
            settings[key] = value

        return settings

    def _parse_header(self, stream: Any) -> dict[str, Any]:
        """
        Parse 32-byte header from summary file.

        Header format:
        - 4 bytes: magic number (0xC73216AB)
        - 2 bytes: version (18)
        - 2 bytes: file type (0 for summary)
        - 4 bytes: machine ID
        - 4 bytes: session ID
        - 8 bytes: first timestamp (ms since epoch)
        - 8 bytes: last timestamp (ms since epoch)

        Returns:
            Dictionary with header fields

        Raises:
            OscarSummaryParseError: If header is invalid
        """
        header_data = stream.read(32)
        if len(header_data) != 32:
            raise OscarSummaryParseError("File too short to contain header")

        (
            magic,
            version,
            file_type,
            machine_id,
            session_id,
            first_timestamp,
            last_timestamp,
        ) = struct.unpack("<IHH II qq", header_data)

        if magic != OSCAR_MAGIC_NUMBER:
            raise OscarSummaryParseError(
                f"Invalid magic number: 0x{magic:08x} (expected 0x{OSCAR_MAGIC_NUMBER:08x})"
            )

        if file_type != 0:
            raise OscarSummaryParseError(
                f"Invalid file type: {file_type} (expected 0 for summary)"
            )

        return {
            "magic": magic,
            "version": version,
            "file_type": file_type,
            "machine_id": machine_id,
            "session_id": session_id,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
        }


def parse_summary_file(file_path: Path) -> SessionSummary:
    """
    Convenience function to parse a summary file.

    Args:
        file_path: Path to .000 file

    Returns:
        SessionSummary object

    Raises:
        OscarSummaryParseError: If parsing fails
    """
    parser = OscarSummaryParser(file_path)
    return parser.parse()
