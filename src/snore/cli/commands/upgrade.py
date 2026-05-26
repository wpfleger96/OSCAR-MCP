"""upgrade command — upgrade SNORE to the latest version."""

from __future__ import annotations

import click

from snore.bootstrap import check_tool_updates, get_tool_source, perform_update
from snore.cli.display import console, print_success


@click.command()
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.option("--force", is_flag=True, help="Force reinstall")
def upgrade(check: bool, force: bool) -> None:
    """Upgrade SNORE to the latest version."""
    source = get_tool_source("snore")
    source_name = {"github": "GitHub", "pypi": "PyPI", "local": "local"}.get(
        source or "", "PyPI"
    )

    console.print(f"Checking for updates from {source_name}...")

    update_info = check_tool_updates()

    if not update_info:
        raise click.ClickException("Could not check for updates")

    if update_info.has_update:
        console.print(
            f"Update available: {update_info.current_version} → {update_info.latest_version}"
        )

        if check:
            console.print("\nRun 'snore upgrade' to install")
            return

        if not force:
            if not click.confirm("Install update?", default=True):
                console.print("Cancelled")
                return

        console.print("Upgrading...")
        success, message, was_upgraded = perform_update(
            force=force, target_version=update_info.latest_version
        )

        if success:
            if was_upgraded:
                print_success(message)
            else:
                from snore.bootstrap.version import get_package_version

                try:
                    new_version = get_package_version("snore")
                except Exception:
                    new_version = update_info.current_version
                if new_version == update_info.current_version:
                    console.print(
                        f"[yellow]⚠ Upgrade reported success but version unchanged "
                        f"({update_info.current_version})[/yellow]"
                    )
                    console.print(
                        "[dim]This may be due to a Python version mismatch. "
                        "Check the package's requires-python and try: "
                        "uv tool upgrade snore --python <version>[/dim]"
                    )
                else:
                    print_success("Already up to date")
        else:
            raise click.ClickException(message)
    else:
        print_success("Already up to date")
