"""Log file management commands."""

from __future__ import annotations

import glob
import subprocess

from collections import deque
from pathlib import Path

import click

from snore.cli.display import console, print_raw, print_warning
from snore.logging_config import get_log_path


@click.group()
def logs() -> None:
    """Log file management commands."""
    pass


@logs.command("path")
def logs_path() -> None:
    """Show log file location."""
    log_path = get_log_path()
    console.print(f"Log file: {log_path}")

    if log_path.exists():
        size_mb = log_path.stat().st_size / (1024 * 1024)
        console.print(f"Size: {size_mb:.2f} MB")

        log_dir = log_path.parent
        backup_files = sorted(glob.glob(str(log_dir / "snore.log.*")))
        if backup_files:
            console.print(f"Backup files: {len(backup_files)}")
    else:
        console.print("(File does not exist yet)")


@logs.command("show")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output (like tail -f)")
def logs_show(lines: int, follow: bool) -> None:
    """Show recent log entries."""
    log_path = get_log_path()

    if not log_path.exists():
        raise click.ClickException("No log file found")

    if follow:
        try:
            subprocess.run(["tail", "-f", str(log_path)], check=True)
        except KeyboardInterrupt:
            pass
        except FileNotFoundError as e:
            raise click.ClickException("'tail' command not found") from e
    else:
        try:
            with open(log_path, encoding="utf-8") as f:
                display_lines = deque(f, maxlen=lines)
                for line in display_lines:
                    print_raw(line.rstrip())
        except Exception as e:
            raise click.ClickException(f"Error reading log file: {e}") from e


@logs.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear all log files?")
def logs_clear() -> None:
    """Clear all log files."""
    log_path = get_log_path()
    log_dir = log_path.parent

    if not log_dir.exists():
        console.print("No log directory found")
        return

    log_files = [log_path] + [Path(f) for f in glob.glob(str(log_dir / "snore.log.*"))]

    removed_count = 0
    for log_file in log_files:
        if log_file.exists():
            try:
                log_file.unlink()
                removed_count += 1
            except Exception as e:
                print_warning(f"Failed to remove {log_file}: {e}")

    if removed_count > 0:
        console.print(f"Removed {removed_count} log file(s)")
    else:
        console.print("No log files to remove")
