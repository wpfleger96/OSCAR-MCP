"""sweep-thresholds command — offline FL/RERA query-time tuning sweep.

Loads stored data once and re-scores a Cartesian grid of query-time tunables
against one of three reference targets, ranking the results.  It measures only:
no algorithm or threshold in the codebase is changed, and nothing is written to
the database.
"""

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
from snore.cli.display import console, err_console
from snore.validation.sweep import SweepResult


def _parse_floats(raw: str | None) -> list[float] | None:
    """Parse a comma-separated list of numbers into floats (None when absent)."""
    if raw is None:
        return None
    try:
        return [float(v.strip()) for v in raw.split(",") if v.strip()]
    except ValueError as e:
        raise click.BadParameter(
            f"Invalid numeric list: {raw!r}. Expected comma-separated numbers."
        ) from e


# CLI option name -> grid knob name.  Options irrelevant to the chosen target
# are silently ignored (the knob is simply absent from that target's grid).
_OPTION_TO_KNOB = {
    "fl_class_threshold_raw": "fl_class_threshold",
    "min_fl_run_length_raw": "min_fl_run_length",
    "recovery_margin_raw": "recovery_amplitude_margin",
    "flg_low_raw": "flg_low_threshold",
    "flg_high_raw": "flg_high_threshold",
}


def _fmt(v: float | int | None) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _render_table(result: SweepResult, top: int) -> None:
    """Render the ranked grid as a Rich table, current-defaults row highlighted."""
    knob_cols = list(result.rows[0].knobs.keys())
    table = Table(
        title=f"Top {min(top, len(result.rows))} of {len(result.rows)} "
        f"(ranked by {result.objective_label})"
    )
    table.add_column("#", justify="right")
    for knob in knob_cols:
        table.add_column(knob, justify="right")
    table.add_column("objective", justify="right")
    for col in result.metric_columns:
        table.add_column(col, justify="right")

    for rank, row in enumerate(result.rows[:top], start=1):
        marker = " (default)" if row.is_default else ""
        cells = [f"{rank}{marker}"]
        cells += [_fmt(row.knobs[k]) for k in knob_cols]
        cells.append(_fmt(row.objective))
        cells += [_fmt(row.metrics.get(c)) for c in result.metric_columns]
        table.add_row(*cells, style="bold cyan" if row.is_default else None)

    console.print(table)
    console.print("[bold cyan]Bold cyan[/bold cyan] row = current defaults.")


@click.command()
@date_range_options_required
@click.option(
    "--target",
    type=click.Choice(["flg", "re", "apple"]),
    required=True,
    help="Reference target: flg (device FLG AUC), re (machine RE), apple (Apple BD)",
)
@click.option(
    "--export",
    type=click.Path(),
    help="Write the FULL ranked grid to a .csv file",
)
@click.option(
    "--fl-class-threshold",
    "fl_class_threshold_raw",
    help="RERA-proxy flow_class threshold values (comma-separated; re/apple)",
)
@click.option(
    "--min-fl-run-length",
    "min_fl_run_length_raw",
    help="RERA-proxy minimum FL run length values (comma-separated; re/apple)",
)
@click.option(
    "--recovery-margin",
    "recovery_margin_raw",
    help="RERA-proxy recovery amplitude margin values (comma-separated; re/apple)",
)
@click.option(
    "--flg-low",
    "flg_low_raw",
    help="FLG AUC low-breakpoint values (comma-separated; flg)",
)
@click.option(
    "--flg-high",
    "flg_high_raw",
    help="FLG AUC high-breakpoint values (comma-separated; flg)",
)
@click.option(
    "--top",
    type=int,
    default=15,
    show_default=True,
    help="Number of top-ranked rows to display (export always writes all)",
)
@db_option
@actor_options
def sweep_thresholds(
    date_from: datetime,
    date_to: datetime,
    target: str,
    export: str | None,
    fl_class_threshold_raw: str | None,
    min_fl_run_length_raw: str | None,
    recovery_margin_raw: str | None,
    flg_low_raw: str | None,
    flg_high_raw: str | None,
    top: int,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """
    Offline threshold-sweep harness for FL/RERA tuning.

    Loads stored breaths (and, per target, FLG waveforms / machine RE / Apple
    nightly signals) once, then re-scores a grid of query-time tunables and
    ranks the results.  Around current defaults the near-chance FL/RERA numbers
    reproduce — the point is that future tuning becomes data-driven, not that
    this run improves anything.
    """
    if date_from > date_to:
        raise click.ClickException("--from date must be before or equal to --to date")
    if db and not Path(db).expanduser().exists():
        raise click.ClickException(f"Database not found: {db}")
    if export and Path(export).suffix != ".csv":
        raise click.ClickException("--export path must end in .csv")

    overrides = {
        "fl_class_threshold_raw": fl_class_threshold_raw,
        "min_fl_run_length_raw": min_fl_run_length_raw,
        "recovery_margin_raw": recovery_margin_raw,
        "flg_low_raw": flg_low_raw,
        "flg_high_raw": flg_high_raw,
    }

    async def _run() -> None:
        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.validation.sweep import (  # noqa: PLC0415
            DEFAULT_GRIDS,
            evaluate_grid,
            export_sweep_csv,
            load_sweep_data,
        )

        grid = {k: list(v) for k, v in DEFAULT_GRIDS[target].items()}
        for opt_name, raw in overrides.items():
            values = _parse_floats(raw)
            if values is None:
                continue
            knob = _OPTION_TO_KNOB[opt_name]
            if knob in grid:
                grid[knob] = values

        async with open_db_session(db) as async_db:
            try:
                profile_id = await resolve_cli_profile_id(
                    async_db, actor_user, actor_profile
                )
                console.print(
                    f"Sweeping target={target} from {date_from.date()} "
                    f"to {date_to.date()}..."
                )
                data = await load_sweep_data(
                    async_db,
                    profile_id,
                    date_from.strftime("%Y-%m-%d"),
                    date_to.strftime("%Y-%m-%d"),
                    target,
                )
                result = evaluate_grid(data, grid)

                console.print(
                    f"\nLoaded {result.n_units_loaded} {result.unit_label}; "
                    f"evaluated {len(result.rows)} grid combinations."
                )
                console.print(f"[dim]{result.notice}[/dim]")

                if result.reference:
                    ref = ", ".join(
                        f"{k}={_fmt(v)}" for k, v in result.reference.items()
                    )
                    console.print(f"[dim]Reference: {ref}[/dim]")

                if not result.rows or result.n_units_loaded == 0:
                    console.print("\n[yellow]No data to sweep in this range.[/yellow]")
                else:
                    _render_table(result, top)

                if export:
                    export_path = Path(export)
                    export_sweep_csv(result, export_path)
                    console.print(
                        f"\nFull ranked grid ({len(result.rows)} rows) exported to "
                        f"{export_path}"
                    )

            except click.ClickException:
                raise
            except Exception as e:
                import traceback  # noqa: PLC0415

                err_console.print(f"Sweep error: {e}")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    traceback.print_exc()
                raise click.ClickException(str(e)) from e

    asyncio.run(_run())
