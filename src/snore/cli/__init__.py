"""Command-line interface for SNORE."""

import logging

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

import click

from snore.cli.display import console
from snore.logging_config import setup_logging

logger = logging.getLogger(__name__)

try:
    __version__ = get_version("snore")
except PackageNotFoundError:
    __version__ = "dev"


def _version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    console.print(f"snore, version {__version__}")

    try:
        from snore.bootstrap import check_tool_updates

        update_info = check_tool_updates(timeout=3)
        if update_info and update_info.has_update:
            console.print(
                f"\nUpdate available: {update_info.current_version} → {update_info.latest_version}"
            )
            console.print("Run 'snore upgrade' to install")
    except Exception as e:
        logger.debug(f"Failed to check for updates: {e}")

    ctx.exit()


@click.group()
@click.option(
    "--version",
    is_flag=True,
    callback=_version_callback,
    expose_value=False,
    is_eager=True,
    help="Show version and check for updates",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose: bool) -> None:
    """SNORE: CPAP Data Management Tool"""
    setup_logging(verbose=verbose, show_time=False)


def _register_commands() -> None:
    from snore.cli.commands.import_data import import_data
    from snore.cli.commands.mcp import mcp
    from snore.cli.commands.serve import serve
    from snore.cli.commands.setup import setup
    from snore.cli.commands.stats import stats
    from snore.cli.commands.upgrade import upgrade
    from snore.cli.commands.validate import validate
    from snore.cli.commands.validate_apple import validate_apple
    from snore.cli.commands.validate_breaths import validate_breaths
    from snore.cli.commands.validate_fl import validate_fl
    from snore.cli.commands.validate_rera import validate_rera
    from snore.cli.groups.analysis import analysis
    from snore.cli.groups.completions import completions
    from snore.cli.groups.db import db
    from snore.cli.groups.export import export
    from snore.cli.groups.health import health
    from snore.cli.groups.logs import logs
    from snore.cli.groups.profile import profile
    from snore.cli.groups.report import report
    from snore.cli.groups.rx import rx
    from snore.cli.groups.session import session
    from snore.cli.groups.user import user
    from snore.cli.groups.waveform import waveform

    cli.add_command(setup)
    cli.add_command(upgrade)
    cli.add_command(import_data, name="import")
    cli.add_command(stats)
    cli.add_command(validate)
    cli.add_command(validate_fl, name="validate-fl")
    cli.add_command(validate_rera, name="validate-rera")
    cli.add_command(validate_apple, name="validate-apple")
    cli.add_command(validate_breaths, name="validate-breaths")
    cli.add_command(serve)
    cli.add_command(mcp)

    cli.add_command(db)
    cli.add_command(session)
    cli.add_command(analysis)
    cli.add_command(health)
    cli.add_command(completions)
    cli.add_command(logs)
    cli.add_command(report)
    cli.add_command(waveform)
    cli.add_command(rx)
    cli.add_command(export)
    cli.add_command(user)
    cli.add_command(profile)


_register_commands()


def main() -> None:
    """Main CLI entry point."""
    cli()
