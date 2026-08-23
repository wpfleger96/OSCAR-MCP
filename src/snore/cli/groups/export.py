"""Export CPAP data in various formats."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import (
    CliCtx,
    actor_options,
    date_range_options,
    db_option,
    device_option,
    init_db,
    profile_scoped_command,
)
from snore.cli.display import console, print_dry_run_header, print_warning
from snore.services.export_service import ExportService


@click.group()
def export() -> None:
    """Export CPAP data in various formats."""
    pass


@export.command("raw")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Destination directory or .zip file (default: ./snore_export_raw)",
)
@date_range_options
@device_option
@click.option("--zip", "as_zip", is_flag=True, help="Force zip output")
@click.option("--dry-run", is_flag=True, help="Show what would be exported")
@click.option(
    "--trim-str",
    is_flag=True,
    help="Trim STR.edf to only include the exported date range",
)
@db_option
@actor_options
def export_raw(
    output: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    device: str | None,
    as_zip: bool,
    dry_run: bool,
    trim_str: bool,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """Export raw SD card files for import into OSCAR.

    Reconstructs an OSCAR-compatible directory structure from backed-up
    raw files. Requires raw backup to have been performed during import.
    The backup source root is always derived from the active profile — it
    cannot be overridden to prevent cross-profile file access.

    Examples:
        snore export raw --from 2025-08-01 --to 2025-08-14
        snore export raw -o ~/Desktop/export.zip --zip
    """
    from snore.database.session import session_scope  # noqa: PLC0415

    if trim_str and not (date_from and date_to):
        raise click.ClickException("--trim-str requires both --from and --to")

    if trim_str and (as_zip or (output and str(output).endswith(".zip"))):
        raise click.ClickException("--trim-str is not supported with zip output")

    if output is None:
        output = "snore_export_raw.zip" if as_zip else "snore_export_raw"

    init_db(db)

    async def _resolve_profile_id() -> int:
        async with session_scope() as db_session:
            from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415

            return await resolve_cli_profile_id(db_session, actor_user, actor_profile)

    import asyncio  # noqa: PLC0415

    profile_id = asyncio.run(_resolve_profile_id())
    svc = ExportService(profile_id)

    try:
        result = svc.export_raw(
            output=Path(output),
            date_from=date_from.date() if date_from else None,
            date_to=date_to.date() if date_to else None,
            device_serial=device,
            as_zip=as_zip,
            dry_run=dry_run,
            trim_str=trim_str,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e

    if dry_run:
        print_dry_run_header("written")

    console.print(f"Nights: {result.nights_exported}")
    console.print(f"Files:  {result.files_written}")
    if result.total_bytes:
        mb = result.total_bytes / (1024 * 1024)
        console.print(f"Size:   {mb:.1f} MB")
    console.print(f"Output: {result.output_path}")

    for w in result.warnings:
        print_warning(w)


@export.command("csv")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Destination directory for CSV files (default: ./snore_export_csv)",
)
@date_range_options
@device_option
@click.option(
    "--include-waveforms",
    is_flag=True,
    help="Include per-session waveform CSV files (large!)",
)
@profile_scoped_command
async def export_csv(
    ctx: CliCtx,
    output: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    device: str | None,
    include_waveforms: bool,
) -> None:
    """Export parsed data as CSV files (sessions, events, settings).

    Creates sessions.csv, events.csv, and settings.csv in the output directory.
    Optionally includes per-session waveform files with --include-waveforms.
    """
    if output is None:
        output = "snore_export_csv"

    svc = ExportService(profile_id=ctx.profile_id)
    try:
        result = await svc.export_csv(
            db_session=ctx.db,
            output=Path(output),
            date_from=date_from.date() if date_from else None,
            date_to=date_to.date() if date_to else None,
            device_serial=device,
            include_waveforms=include_waveforms,
        )
    except Exception as e:
        raise click.ClickException(str(e)) from e

    console.print(f"Nights: {result.nights_exported}")
    console.print(f"Files:  {result.files_written}")
    console.print(f"Output: {result.output_path}")

    for w in result.warnings:
        print_warning(w)


@export.command("json")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output JSON file path (default: ./snore_export.json)",
)
@date_range_options
@device_option
@profile_scoped_command
async def export_json(
    ctx: CliCtx,
    output: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    device: str | None,
) -> None:
    """Export parsed data as a JSON document.

    Creates a single JSON file with sessions, events, statistics, and settings.
    """
    if output is None:
        output = "snore_export.json"

    svc = ExportService(profile_id=ctx.profile_id)
    try:
        result = await svc.export_json(
            db_session=ctx.db,
            output=Path(output),
            date_from=date_from.date() if date_from else None,
            date_to=date_to.date() if date_to else None,
            device_serial=device,
        )
    except Exception as e:
        raise click.ClickException(str(e)) from e

    console.print(f"Nights: {result.nights_exported}")
    console.print(f"Output: {result.output_path}")

    for w in result.warnings:
        print_warning(w)
