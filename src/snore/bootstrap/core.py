"""Shared constants and helpers for bootstrap installer/updater."""

import shutil
import subprocess

UV_NOT_FOUND_ERROR = "uv not found in PATH. Install from https://docs.astral.sh/uv/"
PACKAGE_NAME = "snore"
GITHUB_REPO = "wpfleger96/SNORE"
GITHUB_REPO_URL = f"git+ssh://git@github.com/{GITHUB_REPO}.git"


def is_command_available(command: str) -> bool:
    """Check if a command is available in PATH.

    Args:
        command: Command name to check

    Returns:
        True if command is available, False otherwise
    """
    return shutil.which(command) is not None


def run_uv_tool_command(
    cmd: list[str], action: str, timeout: int = 60
) -> tuple[bool, str, str]:
    """Run a uv tool subprocess with shared error handling.

    Args:
        cmd: Command and arguments to execute
        action: Human-readable action name used in messages
            (e.g. "Installation", "Upgrade")
        timeout: Subprocess timeout in seconds (default: 60)

    Returns:
        Tuple of (success, message, output)
        - success: Whether the command succeeded
        - message: Human-readable status message
        - output: Combined stdout+stderr on success, empty string otherwise
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            return True, f"{action} successful", result.stdout + result.stderr

        error_msg = result.stderr.strip()
        if not error_msg:
            error_msg = f"{action} failed with no error message"

        return False, error_msg, ""

    except subprocess.TimeoutExpired:
        return False, f"{action} timed out after {timeout} seconds", ""
    except Exception as e:
        return False, f"Unexpected error: {e}", ""
