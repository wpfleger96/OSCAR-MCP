"""Centralized logging configuration for SNORE."""

import logging
import logging.config
import os
import sys

from pathlib import Path
from typing import Any

from snore.constants import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_DIR,
    DEFAULT_LOG_FILE,
)

_logging_configured = False


def get_log_dir() -> Path:
    """
    Get log directory path, creating if needed.

    Returns:
        Path to log directory
    """
    log_dir = DEFAULT_LOG_DIR
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    return log_dir


def get_log_path() -> Path:
    """
    Get path to the active log file.

    Returns:
        Path to snore.log
    """
    return get_log_dir() / DEFAULT_LOG_FILE


def _get_user_logging_config() -> dict[str, Any]:
    """
    Load logging settings from config file.

    Returns:
        Dictionary with logging settings, or empty dict if not configured
    """
    return {}


def _build_logging_config(
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Build the dictConfig configuration dictionary.

    The console handler is attached programmatically after dictConfig
    because RichHandler requires a Console instance that cannot be
    expressed as a dictConfig class string.

    Args:
        verbose: If True, set console to DEBUG level

    Returns:
        Dictionary suitable for logging.config.dictConfig()
    """
    user_config = _get_user_logging_config()
    file_enabled = user_config.get("enabled", True)
    file_level = user_config.get("level", "DEBUG").upper()
    max_bytes = user_config.get("max_size_mb", 10) * 1024 * 1024
    backup_count = user_config.get("backup_count", DEFAULT_LOG_BACKUP_COUNT)

    log_file = get_log_path()

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "file": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"},
        },
        "handlers": {},
        "root": {
            "level": "DEBUG",
            "handlers": [],
        },
    }

    if file_enabled:
        config["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": file_level,
            "formatter": "file",
            "filename": str(log_file),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
        }
        config["root"]["handlers"].append("file")

    return config


def setup_logging(
    *,
    verbose: bool = False,
    show_time: bool = True,
) -> None:
    """
    Configure logging for SNORE application.

    Uses dictConfig for file handler and programmatic RichHandler for
    console output so log messages are synchronized with Rich's live
    display system (prevents terminal corruption during progress bars).

    Args:
        verbose: If True, set console to DEBUG level
        show_time: If True, show timestamps in console log output
    """
    global _logging_configured

    if _logging_configured:
        return

    try:
        from rich.logging import RichHandler

        from snore.cli.display import err_console

        config = _build_logging_config(verbose=verbose)
        logging.config.dictConfig(config)

        console_handler = RichHandler(
            console=err_console,
            show_time=show_time,
            show_path=False,
            markup=False,
            rich_tracebacks=True,
        )
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        logging.getLogger().addHandler(console_handler)
    except Exception as e:
        sys.stderr.write(f"WARNING: Failed to configure logging: {e}\n")
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(asctime)s - %(levelname)s: %(message)s"
            if show_time
            else "%(levelname)s: %(message)s",
        )

    _logging_configured = True
