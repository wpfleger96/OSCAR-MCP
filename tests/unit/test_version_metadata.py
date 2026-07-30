"""Drift guards keeping reported versions tied to release-please's inputs.

release-please bumps ``pyproject.toml`` and ``.release-please-manifest.json``
in lockstep on every release. Anything that reports a version must derive it
from package metadata instead of repeating the literal, or it silently drifts
one release after the pipeline goes live.
"""

import json
import tomllib

from importlib.metadata import version as get_version
from pathlib import Path

import pytest

from snore.api.app import create_app

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_api_version_matches_installed_package_metadata() -> None:
    """The FastAPI app reports the installed snore version, not a literal."""
    assert create_app().version == get_version("snore")


def test_release_manifest_version_matches_pyproject() -> None:
    """release-please's manifest baseline agrees with the packaged version."""
    pyproject = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (_REPO_ROOT / ".release-please-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["."] == pyproject["project"]["version"]
