"""Generate HTML reports for CPAP therapy data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import CliCtx, profile_scoped_command
from snore.cli.display import ICON_CHART, print_footer, print_header, print_kv


@click.group()
def report() -> None:
    """Generate HTML reports for CPAP therapy data."""
    pass


@report.command("summary")
@click.option(
    "--from",
    "from_date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--output",
    "-o",
    default="snore-report.html",
    type=click.Path(),
    help="Output file path (default: snore-report.html)",
)
@profile_scoped_command
async def summary(
    ctx: CliCtx,
    from_date: datetime,
    to_date: datetime,
    output: str,
) -> None:
    """Generate a summary HTML report for a date range.

    Examples:
        snore report summary --from 2025-01-01 --to 2025-01-31
        snore report summary --from 2025-01-01 --to 2025-01-31 -o ~/Desktop/report.html
    """
    from snore.services import ReportService  # noqa: PLC0415

    fd = from_date.date()
    td = to_date.date()
    if fd > td:
        raise click.UsageError("--from must not be after --to")

    svc = ReportService(ctx.db, ctx.profile_id)
    html = await svc.generate_summary_report(fd, td)

    out_path = Path(output)
    out_path.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024

    print_header("Summary Report", ICON_CHART)
    print_kv("Output", str(out_path.resolve()))
    print_kv("Size", f"{size_kb:.1f} KB")
    print_kv("Date range", f"{fd} to {td}")
    print_footer()


@report.command("comparison")
@click.option(
    "--from",
    "from_date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date of primary range (YYYY-MM-DD)",
)
@click.option(
    "--to",
    "to_date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date of primary range (YYYY-MM-DD)",
)
@click.option(
    "--compare-from",
    "compare_from",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Start date of comparison range (YYYY-MM-DD)",
)
@click.option(
    "--compare-to",
    "compare_to",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="End date of comparison range (YYYY-MM-DD)",
)
@click.option(
    "--output",
    "-o",
    default="snore-comparison.html",
    type=click.Path(),
    help="Output file path (default: snore-comparison.html)",
)
@profile_scoped_command
async def comparison(
    ctx: CliCtx,
    from_date: datetime,
    to_date: datetime,
    compare_from: datetime,
    compare_to: datetime,
    output: str,
) -> None:
    """Generate a comparison HTML report across two date ranges.

    Examples:
        snore report comparison --from 2025-01-01 --to 2025-01-31 \\
          --compare-from 2025-02-01 --compare-to 2025-02-28
        snore report comparison --from 2025-01-01 --to 2025-01-31 \\
          --compare-from 2025-02-01 --compare-to 2025-02-28 -o ~/Desktop/report.html
    """
    from snore.services import ReportService  # noqa: PLC0415

    fd = from_date.date()
    td = to_date.date()
    cfd = compare_from.date()
    ctd = compare_to.date()

    if fd > td:
        raise click.UsageError("--from must not be after --to")
    if cfd > ctd:
        raise click.UsageError("--compare-from must not be after --compare-to")

    svc = ReportService(ctx.db, ctx.profile_id)
    html = await svc.generate_comparison_report((fd, td), (cfd, ctd))

    out_path = Path(output)
    out_path.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024

    print_header("Comparison Report", ICON_CHART)
    print_kv("Output", str(out_path.resolve()))
    print_kv("Size", f"{size_kb:.1f} KB")
    print_kv("Primary range", f"{fd} to {td}")
    print_kv("Comparison range", f"{cfd} to {ctd}")
    print_footer()
