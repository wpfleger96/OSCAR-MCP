"""setup command — install SNORE globally as a uv tool."""

from __future__ import annotations

import click

from snore.bootstrap import install_tool
from snore.cli.display import console, print_success, print_warning
from snore.completions import detect_shell, install_completion


@click.command()
@click.option("--github", is_flag=True, help="Install from GitHub instead of PyPI")
@click.option("--force", is_flag=True, help="Force reinstall")
@click.option("--dry-run", is_flag=True, help="Show what would be done")
@click.option("--skip-completions", is_flag=True, help="Skip shell completion setup")
def setup(github: bool, force: bool, dry_run: bool, skip_completions: bool) -> None:
    """Install SNORE globally as a uv tool."""
    source_name = "GitHub" if github else "PyPI"
    console.print(f"Installing SNORE from {source_name}...")

    success, message = install_tool(from_github=github, force=force, dry_run=dry_run)

    if success:
        print_success(message)
        console.print("\nYou can now run 'snore' from anywhere!")

        if not skip_completions:
            shell = detect_shell()
            if shell:
                comp_success, comp_msg = install_completion(shell, dry_run=dry_run)
                if comp_success:
                    print_success(f"Shell completions: {comp_msg}")
                else:
                    print_warning(f"Shell completions: {comp_msg}")
            else:
                print_warning("Could not detect shell for completion setup")
                console.print(
                    "  Run 'snore completions install --shell <bash|zsh>' manually"
                )
    else:
        raise click.ClickException(message)
