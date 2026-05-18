"""Shell tab completion management commands."""

from __future__ import annotations

import click

from snore.cli.display import console, print_raw, print_success
from snore.completions import (
    detect_shell,
    find_config_file,
    generate_completion_script,
    install_completion,
    uninstall_completion,
)

_SUPPORTED_SHELLS = ["bash", "zsh"]


@click.group()
def completions() -> None:
    """Manage shell tab completion."""
    pass


@completions.command(name="bash")
def completions_bash() -> None:
    """Output bash completion script for manual installation."""
    try:
        script = generate_completion_script("bash")
        print_raw(script)
        console.print("\nTo install: Add the above to your ~/.bashrc or run:")
        console.print("\nsnore completions install")
    except Exception as e:
        raise click.ClickException(f"Error generating completion script: {e}") from e


@completions.command(name="zsh")
def completions_zsh() -> None:
    """Output zsh completion script for manual installation."""
    try:
        script = generate_completion_script("zsh")
        print_raw(script)
        console.print("\nTo install: Add the above to your ~/.zshrc or run:")
        console.print("\nsnore completions install")
    except Exception as e:
        raise click.ClickException(f"Error generating completion script: {e}") from e


@completions.command(name="install")
@click.option(
    "--shell",
    type=click.Choice(_SUPPORTED_SHELLS, case_sensitive=False),
    help="Shell type (auto-detected if not specified)",
)
def completions_install(shell: str | None) -> None:
    """Install shell completion to config file."""
    if shell is None:
        shell = detect_shell()
        if shell is None:
            raise click.ClickException(
                "Could not detect shell. Please specify with --shell"
            )
        console.print(f"Detected shell: {shell}")

    success, message = install_completion(shell, dry_run=False)

    if success:
        print_success(message)
    else:
        raise click.ClickException(message)


@completions.command(name="uninstall")
@click.option(
    "--shell",
    type=click.Choice(_SUPPORTED_SHELLS, case_sensitive=False),
    help="Shell type (auto-detected if not specified)",
)
def completions_uninstall(shell: str | None) -> None:
    """Remove shell completion from config file."""
    if shell is None:
        shell = detect_shell()
        if shell is None:
            raise click.ClickException(
                "Could not detect shell. Please specify with --shell"
            )

    config_path = find_config_file(shell)
    if config_path is None:
        raise click.ClickException(f"No {shell} config file found")

    success, message = uninstall_completion(config_path)

    if success:
        print_success(message)
    else:
        raise click.ClickException(message)
