"""validate-apple command — cross-source FL/RERA validation against Apple Health."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from pathlib import Path

import click

from rich.table import Table

from snore.cli.decorators import (
    actor_options,
    date_range_options_required,
    db_option,
)
from snore.cli.decorators import (
    db_session as open_db_session,
)
from snore.cli.display import console, err_console, print_header


def _fmt_r(v: float | None) -> str:
    return f"{v:.3f}" if v is not None else "N/A"


@click.command()
@date_range_options_required
@click.option(
    "--device-id",
    type=int,
    default=None,
    help="Pin SNORE device id (disambiguates multi-device nights)",
)
@click.option(
    "--export",
    type=click.Path(),
    help="Export report to file (.json or .csv)",
)
@db_option
@actor_options
def validate_apple(
    date_from: datetime,
    date_to: datetime,
    device_id: int | None,
    export: str | None,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """
    Validate SNORE FL/RERA nightly indices against independent Apple Health signals.

    Correlates rera_index and fl_class_ge4_pct against Apple's sleeping-breathing-
    disturbance metric and watch-derived fragmentation (awake_seconds,
    sleep_efficiency_pct) using per-pair Spearman correlations.  Nights whose
    SNORE analysis has not run or is stale, and nights lacking Apple data, are
    reported as skipped.
    """
    if date_from > date_to:
        raise click.ClickException("--from date must be before or equal to --to date")

    if db and not Path(db).expanduser().exists():
        raise click.ClickException(f"Database not found: {db}")

    async def _run() -> None:
        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.validation import (  # noqa: PLC0415
            AppleCrossValidator,
            export_apple_cross_report_csv,
            export_apple_cross_report_json,
        )

        async with open_db_session(db) as async_db:
            try:
                profile_id = await resolve_cli_profile_id(
                    async_db, actor_user, actor_profile
                )
                validator = AppleCrossValidator(async_db, profile_id)

                console.print(
                    f"Running Apple cross-source validation from "
                    f"{date_from.date()} to {date_to.date()}..."
                )

                report = await validator.validate_date_range(
                    date_from.strftime("%Y-%m-%d"),
                    date_to.strftime("%Y-%m-%d"),
                    device_id=device_id,
                )

                agg = report.aggregate

                print_header("APPLE CROSS-SOURCE VALIDATION REPORT")
                console.print(
                    f"Date Range: {report.date_range_start} to {report.date_range_end}"
                )
                console.print(f"Total Nights:               {agg.total_nights}")
                console.print(f"Nights w/ Apple BD:         {agg.n_with_apple_bd}")
                console.print(
                    f"Skipped (no Apple BD):      {agg.n_skipped_no_apple_bd}"
                )
                console.print(f"Skipped (analysis not run): {agg.n_analysis_not_run}")
                console.print(f"Skipped (analysis stale):   {agg.n_analysis_stale}")
                console.print(f"Skipped (device ambiguous): {agg.n_device_ambiguous}")

                corr_table = Table(title="Cross-source Spearman correlations")
                corr_table.add_column("Metric pair")
                corr_table.add_column("rho", justify="right")
                corr_table.add_column("p", justify="right")
                corr_table.add_column("n", justify="right")
                corr_table.add_column("reason")
                for label, pair in (
                    ("rera_index vs apple_bd", agg.rera_vs_apple_bd),
                    ("fl_ge4_pct vs apple_bd", agg.fl_vs_apple_bd),
                    ("rera_index vs awake_s", agg.rera_vs_awake_seconds),
                    ("fl_ge4_pct vs sleep_eff", agg.fl_vs_sleep_efficiency),
                ):
                    corr_table.add_row(
                        label,
                        _fmt_r(pair.rho),
                        _fmt_r(pair.p_value),
                        str(pair.n_paired_nights),
                        pair.reason or "",
                    )
                console.print(corr_table)

                if export:
                    export_path = Path(export)
                    if export_path.suffix == ".json":
                        export_apple_cross_report_json(report, export_path)
                    elif export_path.suffix == ".csv":
                        export_apple_cross_report_csv(report, export_path)
                    else:
                        raise click.ClickException(
                            f"Unknown export format '{export_path.suffix}'. "
                            "Use .json or .csv"
                        )
                    console.print(f"\nReport exported to {export_path}")

            except click.ClickException:
                raise
            except Exception as e:
                import traceback  # noqa: PLC0415

                err_console.print(f"Apple cross-source validation error: {e}")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    traceback.print_exc()
                raise click.ClickException(str(e)) from e

    asyncio.run(_run())
