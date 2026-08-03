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
        logger.debug("Parser registry initialized")

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

        for existing in self._parsers:
            if existing.parser_id == parser_id:
                raise ValueError(
                    f"Parser ID '{parser_id}' already registered by {existing.__class__.__name__}"
                )

        self._parsers.append(parser)

        logger.debug(f"Registered parser: {parser}")

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

    def list_supported_models(self) -> list[str]:
        """
        Return the union of supported_models across all registered parsers.

        Returns:
            Sorted, deduplicated list of supported device model strings.

        Example:
            models = registry.list_supported_models()
            # ["AirCurve 11 VAuto", "AirSense 10 AutoSet", ...]
        """
        seen: set[str] = set()
        for parser in self._parsers:
            for model in parser.metadata.supported_models:
                seen.add(model)
        return sorted(seen)

    def detect_parser(self, path: Path) -> DeviceParser | None:
        """
        Auto-detect which parser can handle the data at the given path.

        Tries all registered parsers and returns the one with highest confidence.
        If multiple parsers match with equal confidence, the first one wins.

        Args:
            path: Path to data directory/file

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

        best_match: tuple[DeviceParser, ParserDetectionResult] | None = None

        for parser in self._parsers:
            try:
                result = parser.detect(path)

                if result.detected:
                    logger.debug(
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
            logger.debug(
                f"Selected parser: {parser.parser_id} (confidence: {result.confidence})"
            )
            return parser

        logger.warning(f"No parser detected for path: {path}")
        return None

    def detect_all_parsers(
        self, path: Path
    ) -> list[tuple[DeviceParser, ParserDetectionResult]]:
        """
        Detect all parsers that can handle the data at the given path.

        Unlike detect_parser which returns only the best match, this method
        returns all parsers that successfully detect the data, sorted by
        confidence (highest first).

        Useful for discovering multiple profiles or data sources.

        Args:
            path: Path to data directory/file

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

        matches = []

        for parser in self._parsers:
            try:
                result = parser.detect(path)

                if result.detected:
                    logger.debug(
                        f"Parser {parser.parser_id} detected data with confidence {result.confidence}"
                    )
                    matches.append((parser, result))

            except Exception as e:
                logger.warning(f"Parser {parser.parser_id} detection failed: {e}")
                continue

        matches.sort(key=lambda x: x[1].confidence, reverse=True)

        if matches:
            logger.debug(f"Found {len(matches)} parser(s) for path: {path}")
        else:
            logger.warning(f"No parsers detected for path: {path}")

        return matches


parser_registry = ParserRegistry()
