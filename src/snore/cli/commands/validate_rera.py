"""validate-rera command — score both RERA definitions against machine RE."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import (
    actor_options,
    date_range_options_required,
    db_option,
)
from snore.cli.decorators import (
    db_session as open_db_session,
)
from snore.cli.display import console, err_console, print_footer, print_header


def _fmt_sig(v: float | None, *, na: str = "N/A") -> str:
    """Adaptive formatting so tiny magnitudes stay visible.

    The chance-precision floor (~4e-5) and proxy precision (~1e-3) collapse to
    ``0.000`` at fixed 3 decimals — comparing floor vs precision is the whole
    point of the field, so render small values with more decimals or in
    scientific notation.  The report model and JSON/CSV exports keep the full
    float; this only affects the terminal display.
    """
    if v is None:
        return na
    if v == 0.0:
        return "0"
    a = abs(v)
    if a >= 0.1:
        return f"{v:.3f}"
    if a >= 1e-3:
        return f"{v:.4f}"
    return f"{v:.2e}"


@click.command()
@date_range_options_required
@click.option(
    "--export",
    type=click.Path(),
    help="Export report to file (.json or .csv)",
)
@db_option
@actor_options
def validate_rera(
    date_from: datetime,
    date_to: datetime,
    export: str | None,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """
    Validate SNORE's two RERA definitions against machine-flagged RE events.

    Scores the amplitude-crescendo detector (mode_result.reras) and the
    query-time FL-run proxy (recomputed from stored breaths) independently
    against the device's RE events.  ResMed flags RE conservatively, so most
    sessions have zero machine RE and are reported as skipped; near-zero
    precision on the rest is expected — read it against the aggregate's
    chance-precision floor.
    """
    if date_from > date_to:
        raise click.ClickException("--from date must be before or equal to --to date")

    if db and not Path(db).expanduser().exists():
        raise click.ClickException(f"Database not found: {db}")

    async def _run() -> None:
        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.validation import (  # noqa: PLC0415
            ReraValidator,
            export_rera_report_csv,
            export_rera_report_json,
        )

        async with open_db_session(db) as async_db:
            try:
                profile_id = await resolve_cli_profile_id(
                    async_db, actor_user, actor_profile
                )
                validator = ReraValidator(async_db, profile_id)

                console.print(
                    f"Running RERA validation from {date_from.date()} "
                    f"to {date_to.date()}..."
                )

                report = await validator.validate_date_range(
                    date_from.strftime("%Y-%m-%d"),
                    date_to.strftime("%Y-%m-%d"),
                )

                agg = report.aggregate

                _fmt_r = _fmt_sig

                print_footer()
                print_header("RERA VALIDATION REPORT")
                console.print(
                    f"Date Range: {report.date_range_start} to {report.date_range_end}"
                )
                console.print(f"Total Sessions:          {agg.total_sessions}")
                console.print(
                    f"Sessions with machine RE: {agg.sessions_with_machine_re}"
                )
                console.print(
                    f"Skipped (no machine RE):  {agg.sessions_skipped_no_machine_re}"
                )
                console.print(
                    f"Skipped (no analysis):    {agg.sessions_skipped_no_analysis}"
                )
                console.print(
                    f"Skipped (no breaths):     {agg.sessions_skipped_no_valid_breaths}"
                )
                console.print(f"Skipped (error):          {agg.sessions_skipped_error}")

                console.print("\nEvent counts / densities (per therapy hour):")
                console.print(
                    f"  Machine RE:  {agg.total_machine_re:<6} "
                    f"({_fmt_r(agg.machine_re_density)}/h)"
                )
                console.print(
                    f"  Amplitude:   {agg.total_amplitude_reras:<6} "
                    f"({_fmt_r(agg.amplitude_density)}/h)"
                )
                console.print(
                    f"  FL-run proxy:{agg.total_proxy_reras:<6} "
                    f"({_fmt_r(agg.proxy_density)}/h)"
                )
                console.print(
                    f"  Chance-precision floor (tol={agg.match_tolerance_seconds}s): "
                    f"{_fmt_r(agg.chance_precision_floor)}"
                )

                if agg.sessions_with_machine_re > 0:
                    console.print(
                        "\nMean scores over sessions with machine RE "
                        "(amplitude | proxy):"
                    )
                    console.print(
                        f"  Sensitivity: {_fmt_r(agg.mean_amplitude_sensitivity)} | "
                        f"{_fmt_r(agg.mean_proxy_sensitivity)}"
                    )
                    console.print(
                        f"  Precision:   {_fmt_r(agg.mean_amplitude_precision)} | "
                        f"{_fmt_r(agg.mean_proxy_precision)}"
                    )
                    console.print(
                        f"  F1:          {_fmt_r(agg.mean_amplitude_f1)} | "
                        f"{_fmt_r(agg.mean_proxy_f1)}"
                    )

                # Most-informative rows lead: most machine RE first.
                scored = sorted(
                    (s for s in report.sessions if s.skipped_reason is None),
                    key=lambda s: s.machine_re_count,
                    reverse=True,
                )
                _table_cap = 20
                if scored:
                    shown = scored[:_table_cap]
                    if len(scored) > _table_cap:
                        header = (
                            f"\nScored sessions (top {_table_cap} of {len(scored)} "
                            "by machine RE; amplitude / proxy):"
                        )
                    else:
                        header = (
                            f"\nScored sessions ({len(scored)}; amplitude / proxy):"
                        )
                    console.print(header)
                    console.print(
                        f"{'Date':<12} {'ID':<6} {'RE':<4} "
                        f"{'aSens':<9} {'aPrec':<9} {'pSens':<9} {'pPrec':<9}"
                    )
                    print_footer()
                    for s in shown:
                        console.print(
                            f"{s.date:<12} "
                            f"{s.session_id:<6} "
                            f"{s.machine_re_count:<4} "
                            f"{_fmt_sig(s.amplitude_sensitivity, na='N/A'):<9} "
                            f"{_fmt_sig(s.amplitude_precision, na='N/A'):<9} "
                            f"{_fmt_sig(s.proxy_sensitivity, na='N/A'):<9} "
                            f"{_fmt_sig(s.proxy_precision, na='N/A'):<9}"
                        )
                    if len(scored) > _table_cap:
                        console.print(
                            f"... {len(scored) - _table_cap} more scored sessions "
                            "not shown (use --export for the full set)"
                        )

                if export:
                    export_path = Path(export)
                    if export_path.suffix == ".json":
                        export_rera_report_json(report, export_path)
                        console.print(f"\nReport exported to {export_path}")
                    elif export_path.suffix == ".csv":
                        export_rera_report_csv(report, export_path)
                        console.print(f"\nReport exported to {export_path}")
                    else:
                        raise click.ClickException(
                            f"Unknown export format '{export_path.suffix}'. "
                            "Use .json or .csv"
                        )

            except click.ClickException:
                raise
            except Exception as e:
                import traceback

                err_console.print(f"RERA validation error: {e}")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    traceback.print_exc()
                raise click.ClickException(str(e)) from e

    asyncio.run(_run())
