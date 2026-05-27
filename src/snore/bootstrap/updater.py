"""Update checking and application utilities."""

import json
import logging
import os
import subprocess
import tomllib
import urllib.request

from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from .installer import (
    GITHUB_REPO,
    GITHUB_REPO_URL,
    PACKAGE_NAME,
    UV_NOT_FOUND_ERROR,
    get_tool_source,
    is_command_available,
)
from .version import get_package_version, is_newer

logger = logging.getLogger(__name__)


@dataclass
class UpdateInfo:
    """Information about available updates."""

    has_update: bool
    current_version: str
    latest_version: str
    source: str


def check_pypi_updates(
    package_name: str, current_version: str, timeout: int = 10
) -> UpdateInfo:
    """Check PyPI for newer version.

    Args:
        package_name: Package name on PyPI
        current_version: Currently installed version
        timeout: Request timeout in seconds (default: 10)

    Returns:
        UpdateInfo with update status
    """
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"

        req = urllib.request.Request(url)
        req.add_header("User-Agent", f"{package_name}/{current_version}")

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())

        latest_version = data["info"]["version"]
        has_update = is_newer(latest_version, current_version)

        return UpdateInfo(
            has_update=has_update,
            current_version=current_version,
            latest_version=latest_version,
            source="pypi",
        )

    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        logger.debug(f"PyPI check failed: {e}")
        return UpdateInfo(
            has_update=False,
            current_version=current_version,
            latest_version=current_version,
            source="pypi",
        )


def check_github_updates(
    repo: str, current_version: str, timeout: int = 10
) -> UpdateInfo:
    """Check GitHub tags for newer version.

    Args:
        repo: GitHub repository in format "owner/repo"
        current_version: Currently installed version
        timeout: Request timeout in seconds (default: 10)

    Returns:
        UpdateInfo with update status
    """
    try:
        url = f"https://api.github.com/repos/{repo}/tags"

        req = urllib.request.Request(url)
        req.add_header("User-Agent", f"snore/{current_version}")

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())

        if not data or len(data) == 0:
            return UpdateInfo(
                has_update=False,
                current_version=current_version,
                latest_version=current_version,
                source="github",
            )

        latest_tag = data[0]["name"]
        latest_version = latest_tag.lstrip("v")

        has_update = is_newer(latest_version, current_version)

        return UpdateInfo(
            has_update=has_update,
            current_version=current_version,
            latest_version=latest_version,
            source="github",
        )

    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.debug(f"GitHub check failed: {e}")
        return UpdateInfo(
            has_update=False,
            current_version=current_version,
            latest_version=current_version,
            source="github",
        )


def check_tool_updates(timeout: int = 10) -> UpdateInfo | None:
    """Check for updates - auto-detect PyPI vs GitHub source.

    Args:
        timeout: Request timeout in seconds (default: 10)

    Returns:
        UpdateInfo if tool is installed and update check succeeds, None otherwise
    """
    try:
        current = get_package_version(PACKAGE_NAME)
    except Exception:
        return None

    source = get_tool_source(PACKAGE_NAME)

    if source == "github":
        return check_github_updates(GITHUB_REPO, current, timeout)
    else:
        return check_pypi_updates(PACKAGE_NAME, current, timeout)


def _get_tool_venv_python(package_name: str) -> str | None:
    """Read the Python version from a uv tool's virtual environment."""
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    pyvenv_cfg = Path(data_home) / "uv" / "tools" / package_name / "pyvenv.cfg"
    try:
        for line in pyvenv_cfg.read_text().splitlines():
            if line.startswith("version_info"):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _fetch_requires_python(
    package_name: str,
    version: str,
    github_repo: str,
    source: str,
    timeout: int = 10,
) -> str | None:
    """Fetch the requires-python specifier for a specific package version."""
    try:
        if source == "github":
            url = f"https://raw.githubusercontent.com/{github_repo}/v{version}/pyproject.toml"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "snore")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = tomllib.loads(response.read().decode())
            result: str | None = data.get("project", {}).get("requires-python")
            return result
        else:
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "snore")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode())
            result = data.get("info", {}).get("requires_python")
            return result
    except Exception as e:
        logger.debug(
            f"Failed to fetch requires-python for {package_name}=={version}: {e}"
        )
        return None


def _compute_required_python(
    package_name: str,
    target_version: str,
    github_repo: str,
    source: str,
) -> str | None:
    """Determine if the tool venv needs a Python upgrade for the target version.

    Returns the minimum required Python major.minor (e.g. "3.14") if the
    current venv Python is too old, None otherwise.
    """
    venv_python = _get_tool_venv_python(package_name)
    if not venv_python:
        return None

    requires_python = _fetch_requires_python(
        package_name, target_version, github_repo, source
    )
    if not requires_python:
        return None

    try:
        spec = SpecifierSet(requires_python)
    except InvalidSpecifier:
        return None

    if venv_python in spec:
        return None

    for s in spec:
        if s.operator in (">=", "==", "~="):
            parts = s.version.split(".")
            return f"{parts[0]}.{parts[1]}"
    return None


def perform_update(
    force: bool = False, target_version: str | None = None
) -> tuple[bool, str, bool]:
    """Upgrade SNORE from correct source.

    Args:
        force: Force reinstall even if already up to date

    Returns:
        Tuple of (success, message, was_upgraded)
        - success: Whether command succeeded
        - message: Human-readable status message
        - was_upgraded: True if package was actually upgraded
    """
    if not is_command_available("uv"):
        return False, UV_NOT_FOUND_ERROR, False

    source = get_tool_source(PACKAGE_NAME)

    # Pre-check: detect if the target version needs a newer Python than the venv has.
    # Workaround for https://github.com/astral-sh/uv/issues/18083
    python_flag: str | None = None
    if target_version and source is not None:
        python_flag = _compute_required_python(
            PACKAGE_NAME, target_version, GITHUB_REPO, source
        )

    if source == "github":
        cmd = ["uv", "tool", "install", "--force", "--reinstall", GITHUB_REPO_URL]
    elif source == "local":
        cmd = ["uv", "tool", "install", PACKAGE_NAME, "--force"]
    else:
        if force:
            cmd = ["uv", "tool", "install", PACKAGE_NAME, "--force"]
        else:
            cmd = ["uv", "tool", "install", "--force", "--reinstall", PACKAGE_NAME]

    if python_flag:
        cmd.extend(["--python", python_flag])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            output = result.stdout + result.stderr

            if (
                "Upgraded" in output
                or "Successfully installed" in output
                or "Installed" in output
            ):
                was_upgraded = True
            elif "Nothing to upgrade" in output or "already" in output.lower():
                was_upgraded = False
            else:
                was_upgraded = True

            return True, "Upgrade successful", was_upgraded

        error_msg = result.stderr.strip()
        if not error_msg:
            error_msg = "Upgrade failed with no error message"

        return False, error_msg, False

    except subprocess.TimeoutExpired:
        return False, "Upgrade timed out after 60 seconds", False
    except Exception as e:
        return False, f"Unexpected error: {e}", False
