"""upgrade command — upgrade SNORE to the latest version."""

from __future__ import annotations

import click

from snore.bootstrap import check_tool_updates, get_tool_source, perform_update


@click.command()
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.option("--force", is_flag=True, help="Force reinstall")
def upgrade(check: bool, force: bool) -> None:
    """Upgrade SNORE to the latest version."""
    source = get_tool_source("snore")
    source_name = {"github": "GitHub", "pypi": "PyPI", "local": "local"}.get(
        source or "", "PyPI"
    )

    click.echo(f"Checking for updates from {source_name}...")

    update_info = check_tool_updates()

    if not update_info:
        raise click.ClickException("Could not check for updates")

    if update_info.has_update:
        click.echo(
            f"Update available: {update_info.current_version} → {update_info.latest_version}"
        )

        if check:
            click.echo("\nRun 'snore upgrade' to install")
            return

        if not force:
            if not click.confirm("Install update?", default=True):
                click.echo("Cancelled")
                return

        click.echo("Upgrading...")
        success, message, was_upgraded = perform_update(force=force)

        if success:
            if was_upgraded:
                click.echo(f"✓ {message}")
            else:
                click.echo("✓ Already up to date")
        else:
            raise click.ClickException(message)
    else:
        click.echo("✓ Already up to date")
