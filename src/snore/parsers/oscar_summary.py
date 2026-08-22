"""
OSCAR Summary File Parser

Parses .000 files containing session summary data and statistics.
Format version 18 (current OSCAR version).
"""

from pathlib import Path
from typing import Any

from snore.parsers.oscar_header import parse_oscar_header
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
        Parse 32-byte header from summary file (file type 0).

        Delegates to the shared OSCAR header parser; see
        ``parse_oscar_header`` for the field layout.
        """
        return parse_oscar_header(
            stream, expected_file_type=0, error_cls=OscarSummaryParseError
        )


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
