"""Pytest configuration and fixtures for OSCAR-MCP tests."""

from pathlib import Path
import pytest


def pytest_configure(config):
    """Register custom test markers."""
    config.addinivalue_line("markers", "unit: Unit tests that do not require external dependencies")
    config.addinivalue_line("markers", "parser: Tests for device parsers")


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def resmed_fixture_path(fixtures_dir):
    """Return path to ResMed test data."""
    return fixtures_dir / "resmed_sample"


@pytest.fixture
def resmed_parser():
    """Return a ResMed EDF parser instance."""
    from oscar_mcp.parsers.resmed_edf import ResmedEDFParser

    return ResmedEDFParser()


@pytest.fixture
def parser_registry():
    """Return the global parser registry with parsers registered."""
    from oscar_mcp.parsers.registry import parser_registry
    from oscar_mcp.parsers.register_all import register_all_parsers

    # Explicitly register parsers for testing
    register_all_parsers()

    return parser_registry
