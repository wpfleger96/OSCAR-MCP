"""
Parser Registry System

Central registry for all device parsers. Provides auto-detection,
parser lookup, and unified access to all parser implementations.

Key Features:
- Auto-detect device type from data files
- Register parsers automatically on import
- Query available parsers
- Confidence-based selection when multiple parsers match
"""

import logging

from pathlib import Path

from snore.parsers.base import DeviceParser, ParserDetectionResult

logger = logging.getLogger(__name__)


class ParserRegistry:
    """
    Global registry for all device parsers.

    Parsers self-register by calling register() when their module is imported.
    This allows the system to automatically discover and use all available parsers.

    Usage:
        # In a parser module:
        parser_registry.register(ResmedEDFParser())

        # In application code:
        parser = parser_registry.detect_parser(data_path)
        for session in parser.parse_sessions(data_path):
            process(session)
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._parsers: list[DeviceParser] = []
        self._parsers_by_id: dict[str, DeviceParser] = {}
        self._parsers_by_manufacturer: dict[str, list[DeviceParser]] = {}
        logger.info("Parser registry initialized")

    def register(self, parser: DeviceParser) -> None:
        """
        Register a new parser.

        Args:
            parser: DeviceParser instance to register

        Raises:
            ValueError: If parser ID is already registered

        Example:
            registry.register(ResmedEDFParser())
        """
        parser_id = parser.parser_id

        if parser_id in self._parsers_by_id:
            existing = self._parsers_by_id[parser_id]
            raise ValueError(
                f"Parser ID '{parser_id}' already registered by {existing.__class__.__name__}"
            )

        self._parsers.append(parser)

        self._parsers_by_id[parser_id] = parser

        manufacturer = parser.manufacturer.lower()
        if manufacturer not in self._parsers_by_manufacturer:
            self._parsers_by_manufacturer[manufacturer] = []
        self._parsers_by_manufacturer[manufacturer].append(parser)

        logger.info(f"Registered parser: {parser}")

    def list_parsers(self) -> list[DeviceParser]:
        """
        Get list of all registered parsers.

        Returns:
            List of all DeviceParser instances

        Example:
            for parser in registry.list_parsers():
                print(f"{parser.manufacturer}: {parser.parser_id}")
        """
        return self._parsers.copy()

    def list_manufacturers(self) -> list[str]:
        """
        Get list of all supported manufacturers.

        Returns:
            List of manufacturer names

        Example:
            manufacturers = registry.list_manufacturers()
        """
        return list(set(p.manufacturer for p in self._parsers))

    def detect_parser(
        self, path: Path, manufacturer_hint: str | None = None
    ) -> DeviceParser | None:
        """
        Auto-detect which parser can handle the data at the given path.

        Tries all registered parsers and returns the one with highest confidence.
        If multiple parsers match with equal confidence, the first one wins.

        Args:
            path: Path to data directory/file
            manufacturer_hint: Optional manufacturer name to try first

        Returns:
            DeviceParser that can handle the data, or None if no match

        Example:
            parser = registry.detect_parser(Path("~/CPAP_Data"))
            if parser:
                print(f"Detected: {parser.manufacturer}")
            else:
                print("No compatible parser found")
        """
        path = Path(path)

        if not path.exists():
            logger.warning(f"Path does not exist: {path}")
            return None

        parsers_to_try = []

        if manufacturer_hint:
            hint_lower = manufacturer_hint.lower()
            if hint_lower in self._parsers_by_manufacturer:
                parsers_to_try.extend(self._parsers_by_manufacturer[hint_lower])

        for parser in self._parsers:
            if parser not in parsers_to_try:
                parsers_to_try.append(parser)

        best_match: tuple[DeviceParser, ParserDetectionResult] | None = None

        for parser in parsers_to_try:
            try:
                result = parser.detect(path)

                if result.detected:
                    logger.info(
                        f"Parser {parser.parser_id} detected data with confidence {result.confidence}"
                    )

                    if (
                        best_match is None
                        or result.confidence > best_match[1].confidence
                    ):
                        best_match = (parser, result)

                    if result.confidence >= 1.0:
                        break

            except Exception as e:
                logger.warning(f"Parser {parser.parser_id} detection failed: {e}")
                continue

        if best_match:
            parser, result = best_match
            logger.info(
                f"Selected parser: {parser.parser_id} (confidence: {result.confidence})"
            )
            return parser

        logger.warning(f"No parser detected for path: {path}")
        return None

    def detect_all_parsers(
        self, path: Path, manufacturer_hint: str | None = None
    ) -> list[tuple[DeviceParser, ParserDetectionResult]]:
        """
        Detect all parsers that can handle the data at the given path.

        Unlike detect_parser which returns only the best match, this method
        returns all parsers that successfully detect the data, sorted by
        confidence (highest first).

        Useful for discovering multiple profiles or data sources.

        Args:
            path: Path to data directory/file
            manufacturer_hint: Optional manufacturer name to try first

        Returns:
            List of (DeviceParser, ParserDetectionResult) tuples, sorted by confidence

        Example:
            results = registry.detect_all_parsers(Path("~/OSCAR/Profiles"))
            for parser, detection in results:
                print(f"{parser.manufacturer}: {detection.message}")
                print(f"  Data root: {detection.metadata.get('data_root')}")
        """
        path = Path(path)

        if not path.exists():
            logger.warning(f"Path does not exist: {path}")
            return []

        parsers_to_try = []

        if manufacturer_hint:
            hint_lower = manufacturer_hint.lower()
            if hint_lower in self._parsers_by_manufacturer:
                parsers_to_try.extend(self._parsers_by_manufacturer[hint_lower])

        for parser in self._parsers:
            if parser not in parsers_to_try:
                parsers_to_try.append(parser)

        matches = []

        for parser in parsers_to_try:
            try:
                result = parser.detect(path)

                if result.detected:
                    logger.info(
                        f"Parser {parser.parser_id} detected data with confidence {result.confidence}"
                    )
                    matches.append((parser, result))

            except Exception as e:
                logger.warning(f"Parser {parser.parser_id} detection failed: {e}")
                continue

        matches.sort(key=lambda x: x[1].confidence, reverse=True)

        if matches:
            logger.info(f"Found {len(matches)} parser(s) for path: {path}")
        else:
            logger.warning(f"No parsers detected for path: {path}")

        return matches


parser_registry = ParserRegistry()
