"""Tests for bootstrap updater utilities."""

from __future__ import annotations

import urllib.error

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from snore.bootstrap.updater import (
    _compute_required_python,
    _fetch_requires_python,
    _get_tool_venv_python,
    perform_update,
)


def _mock_urlopen(body: bytes) -> Callable[..., Any]:
    """Create a mock urlopen that returns a context manager with the given body."""

    def _urlopen(*args: Any, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    return _urlopen


@pytest.mark.unit
class TestGetToolVenvPython:
    """Tests for _get_tool_venv_python helper."""

    def test_reads_version_from_pyvenv_cfg(self, tmp_path, monkeypatch):
        pyvenv_cfg = tmp_path / "uv" / "tools" / "test-pkg" / "pyvenv.cfg"
        pyvenv_cfg.parent.mkdir(parents=True)
        pyvenv_cfg.write_text(
            "home = /usr/bin\n"
            "implementation = CPython\n"
            "version_info = 3.13.3\n"
            "include-system-site-packages = false\n"
        )
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert _get_tool_venv_python("test-pkg") == "3.13.3"

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert _get_tool_venv_python("nonexistent-pkg") is None

    def test_returns_none_when_no_version_info(self, tmp_path, monkeypatch):
        pyvenv_cfg = tmp_path / "uv" / "tools" / "test-pkg" / "pyvenv.cfg"
        pyvenv_cfg.parent.mkdir(parents=True)
        pyvenv_cfg.write_text("home = /usr/bin\n")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert _get_tool_venv_python("test-pkg") is None


@pytest.mark.unit
class TestFetchRequiresPython:
    """Tests for _fetch_requires_python helper."""

    def test_pypi_source(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.urllib.request.urlopen",
            _mock_urlopen(b'{"info": {"requires_python": ">=3.14"}}'),
        )
        result = _fetch_requires_python("test-pkg", "1.0.0", "owner/repo", "pypi")
        assert result == ">=3.14"

    def test_github_source(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.urllib.request.urlopen",
            _mock_urlopen(b'[project]\nrequires-python = ">=3.14"\n'),
        )
        result = _fetch_requires_python("test-pkg", "1.0.0", "owner/repo", "github")
        assert result == ">=3.14"

    def test_returns_none_on_network_error(self, monkeypatch):
        def _raise(*args: Any, **kwargs: Any) -> None:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("snore.bootstrap.updater.urllib.request.urlopen", _raise)
        result = _fetch_requires_python("test-pkg", "1.0.0", "owner/repo", "pypi")
        assert result is None


@pytest.mark.unit
class TestComputeRequiredPython:
    """Tests for _compute_required_python helper."""

    def test_returns_none_when_venv_satisfies(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater._get_tool_venv_python",
            lambda pkg: "3.14.5",
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._fetch_requires_python",
            lambda *a: ">=3.14",
        )
        result = _compute_required_python("test-pkg", "2.0.0", "owner/repo", "pypi")
        assert result is None

    def test_returns_minimum_when_venv_too_old(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater._get_tool_venv_python",
            lambda pkg: "3.13.3",
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._fetch_requires_python",
            lambda *a: ">=3.14",
        )
        result = _compute_required_python("test-pkg", "2.0.0", "owner/repo", "pypi")
        assert result == "3.14"

    def test_returns_none_when_venv_unknown(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater._get_tool_venv_python",
            lambda pkg: None,
        )
        result = _compute_required_python("test-pkg", "2.0.0", "owner/repo", "pypi")
        assert result is None

    def test_returns_none_when_requires_python_unknown(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater._get_tool_venv_python",
            lambda pkg: "3.13.3",
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._fetch_requires_python",
            lambda *a: None,
        )
        result = _compute_required_python("test-pkg", "2.0.0", "owner/repo", "pypi")
        assert result is None


@pytest.mark.unit
class TestPerformUpdatePythonSwitch:
    """Tests for perform_update with Python version switching."""

    def test_adds_python_flag_when_mismatch(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.is_command_available", lambda cmd: True
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater.get_tool_source", lambda pkg: "pypi"
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._compute_required_python",
            lambda pkg, ver, repo, src: "3.14",
        )

        captured_cmd = []

        def mock_run(*args, **kwargs):
            captured_cmd.extend(args[0])

            class Result:
                returncode = 0
                stderr = ""
                stdout = "Upgraded snore from 1.0.0 to 2.0.0"

            return Result()

        monkeypatch.setattr("snore.bootstrap.updater.subprocess.run", mock_run)
        success, msg, was_upgraded = perform_update(target_version="2.0.0")
        assert success is True
        assert was_upgraded is True
        assert "--python" in captured_cmd
        assert "3.14" in captured_cmd

    def test_no_python_flag_when_no_mismatch(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.is_command_available", lambda cmd: True
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater.get_tool_source", lambda pkg: "pypi"
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._compute_required_python",
            lambda pkg, ver, repo, src: None,
        )

        captured_cmd = []

        def mock_run(*args, **kwargs):
            captured_cmd.extend(args[0])

            class Result:
                returncode = 0
                stderr = ""
                stdout = "Nothing to upgrade"

            return Result()

        monkeypatch.setattr("snore.bootstrap.updater.subprocess.run", mock_run)
        perform_update(target_version="1.0.0")
        assert "--python" not in captured_cmd

    def test_no_python_flag_when_no_target_version(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.is_command_available", lambda cmd: True
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater.get_tool_source", lambda pkg: "pypi"
        )

        captured_cmd = []

        def mock_run(*args, **kwargs):
            captured_cmd.extend(args[0])

            class Result:
                returncode = 0
                stderr = ""
                stdout = "Nothing to upgrade"

            return Result()

        monkeypatch.setattr("snore.bootstrap.updater.subprocess.run", mock_run)
        perform_update()
        assert "--python" not in captured_cmd

    def test_pypi_upgrade_uses_force_reinstall(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.is_command_available", lambda cmd: True
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater.get_tool_source", lambda pkg: "pypi"
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._compute_required_python",
            lambda pkg, ver, repo, src: None,
        )

        captured_cmd = []

        def mock_run(*args, **kwargs):
            captured_cmd.extend(args[0])

            class Result:
                returncode = 0
                stderr = ""
                stdout = "Installed snore 1.0.0"

            return Result()

        monkeypatch.setattr("snore.bootstrap.updater.subprocess.run", mock_run)
        perform_update(target_version="1.0.0")
        assert "--force" in captured_cmd
        assert "--reinstall" in captured_cmd
        assert "install" in captured_cmd

    def test_github_source_gets_python_flag(self, monkeypatch):
        monkeypatch.setattr(
            "snore.bootstrap.updater.is_command_available", lambda cmd: True
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater.get_tool_source", lambda pkg: "github"
        )
        monkeypatch.setattr(
            "snore.bootstrap.updater._compute_required_python",
            lambda pkg, ver, repo, src: "3.14",
        )

        captured_cmd = []

        def mock_run(*args, **kwargs):
            captured_cmd.extend(args[0])

            class Result:
                returncode = 0
                stderr = ""
                stdout = "Installed 1 executable: snore"

            return Result()

        monkeypatch.setattr("snore.bootstrap.updater.subprocess.run", mock_run)
        success, msg, was_upgraded = perform_update(target_version="2.0.0")
        assert success is True
        assert "--python" in captured_cmd
        assert "3.14" in captured_cmd
        assert "--force" in captured_cmd
        assert "--reinstall" in captured_cmd
