"""Tool installation utilities."""

import os
import tomllib

from pathlib import Path

from .core import (
    GITHUB_REPO_URL,
    PACKAGE_NAME,
    UV_NOT_FOUND_ERROR,
    is_command_available,
    run_uv_tool_command,
)

__all__ = [
    "UV_NOT_FOUND_ERROR",
    "get_tool_source",
    "install_tool",
    "is_command_available",
]


def install_tool(
    from_github: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Install SNORE as a uv tool.

    Args:
        from_github: Install from GitHub instead of PyPI
        force: Force reinstall if already installed
        dry_run: Show what would be done without executing

    Returns:
        Tuple of (success, message)
    """
    if not is_command_available("uv"):
        return False, UV_NOT_FOUND_ERROR

    source = GITHUB_REPO_URL if from_github else PACKAGE_NAME
    cmd = ["uv", "tool", "install", source]

    if force:
        cmd.insert(3, "--force")
        if from_github:
            cmd.insert(4, "--reinstall")

    if dry_run:
        return True, f"Would run: {' '.join(cmd)}"

    success, message, _ = run_uv_tool_command(cmd, "Installation")
    return success, message


def get_tool_source(package_name: str) -> str | None:
    """Detect how a uv tool was installed.

    Args:
        package_name: Name of the uv tool package

    Returns:
        "pypi" if installed from PyPI
        "github" if installed from GitHub
        "local" if installed from local path
        None if tool not installed or can't determine
    """
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    receipt_path = Path(data_home) / "uv" / "tools" / package_name / "uv-receipt.toml"

    if not receipt_path.exists():
        return None

    try:
        with open(receipt_path, "rb") as f:
            receipt = tomllib.load(f)

        requirements = receipt.get("tool", {}).get("requirements", [])
        if not requirements:
            return None

        first_req = requirements[0]
        if isinstance(first_req, dict):
            if "path" in first_req:
                return "local"
            if "git" in first_req and "github.com" in first_req["git"]:
                return "github"

        return "pypi"

    except OSError, tomllib.TOMLDecodeError, KeyError, IndexError:
        return None
