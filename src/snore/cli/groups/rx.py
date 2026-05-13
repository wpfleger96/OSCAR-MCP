"""RX (prescription) settings tracking and analysis commands."""

from __future__ import annotations

import click

from snore.cli.decorators import db_option, init_db


def _format_pressure(settings: dict[str, str]) -> str:
    if "pressure_min" in settings and "pressure_max" in settings:
        return f"{settings['pressure_min']}-{settings['pressure_max']} cmH2O"
    if "pressure_fixed" in settings:
        return f"{settings['pressure_fixed']} cmH2O (Fixed)"
    return "?"


@click.group()
def rx() -> None:
    """RX (prescription) settings tracking and analysis."""
    pass


@rx.command("history")
@db_option
def rx_history(db: str | None) -> None:
    """
    Show RX settings history with average outcomes.

    Displays all prescription periods in chronological order with settings
    and key metrics like average AHI and therapy hours.

    Example:
        snore rx history
    """
    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import session_scope

    init_db(db)

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)

        click.echo("RX Settings History")
        click.echo("=" * 80)

        for i, period in enumerate(stats_periods, 1):
            days_count = len(period.days)
            end_str = (
                period.end_date.strftime("%Y-%m-%d")
                if i < len(stats_periods)
                else "present"
            )

            click.echo(
                f"\nPeriod {i}: {period.start_date.strftime('%Y-%m-%d')} to {end_str} ({days_count} days)"
            )

            mode = period.settings.get("mode", "?")
            epr_level = period.settings.get("epr_level", "?")
            epr_mode = period.settings.get("epr_mode", "?")
            pressure_str = _format_pressure(period.settings)

            click.echo(
                f"  Mode: {mode} | Pressure: {pressure_str} | EPR: {epr_level} {epr_mode}"
            )

            if period.avg_ahi is not None:
                click.echo(f"  Avg AHI: {period.avg_ahi:.1f}", nl=False)
            else:
                click.echo("  Avg AHI: N/A", nl=False)

            if period.avg_hours is not None:
                click.echo(f" | Avg Hours: {period.avg_hours:.1f}", nl=False)

            if period.avg_leak is not None:
                click.echo(f" | Avg Leak: {period.avg_leak:.1f}")
            else:
                click.echo()

        click.echo("\n" + "=" * 80)


@rx.command("current")
@db_option
def rx_current(db: str | None) -> None:
    """
    Show current RX settings period.

    Displays the most recent prescription settings along with outcomes.

    Example:
        snore rx current
    """
    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import session_scope

    init_db(db)

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)
        current = stats_periods[-1]

        days_count = len(current.days)

        click.echo("Current RX Settings")
        click.echo("=" * 80)
        click.echo(
            f"Period: {current.start_date.strftime('%Y-%m-%d')} to present ({days_count} days)"
        )

        mode = current.settings.get("mode", "?")
        epr_level = current.settings.get("epr_level", "?")
        epr_mode = current.settings.get("epr_mode", "?")
        pressure_str = _format_pressure(current.settings)

        click.echo(f"\nMode: {mode}")
        click.echo(f"Pressure: {pressure_str}")
        click.echo(f"EPR: {epr_level} {epr_mode}")

        click.echo("\nOutcomes:")
        if current.avg_ahi is not None:
            click.echo(f"  Avg AHI: {current.avg_ahi:.1f}")
        else:
            click.echo("  Avg AHI: N/A")

        if current.median_ahi is not None:
            click.echo(f"  Median AHI: {current.median_ahi:.1f}")

        if current.avg_hours is not None:
            click.echo(f"  Avg Hours: {current.avg_hours:.1f}")

        if current.avg_leak is not None:
            click.echo(f"  Avg Leak: {current.avg_leak:.1f}")

        click.echo("=" * 80)


@rx.command("compare")
@db_option
@click.option(
    "--min-days",
    type=int,
    default=7,
    help="Minimum days for period to be included (default: 7)",
)
def rx_compare(db: str | None, min_days: int) -> None:
    """
    Compare RX periods and identify best/worst settings.

    Shows a table of all prescription periods with statistics side-by-side
    and highlights the best and worst periods based on average AHI.

    Example:
        snore rx compare
        snore rx compare --min-days 14
    """
    from snore.analysis.rx_tracker import RxTracker
    from snore.database.session import session_scope

    init_db(db)

    with session_scope() as db_session:
        tracker = RxTracker()
        periods = tracker.compute_periods(db_session)

        if not periods:
            click.echo("No RX periods found")
            return

        stats_periods = tracker.compute_period_stats(periods)

        if len(stats_periods) < 2:
            click.echo(
                "At least 2 periods are needed for comparison. Use 'snore rx history' to view the single period."
            )
            return

        best, worst = tracker.best_worst(stats_periods, min_days=min_days)

        click.echo("RX Period Comparison")
        click.echo("=" * 80)
        click.echo(
            f"{'Dates':<25} {'Days':<6} {'Avg AHI':<10} {'Avg Leak':<10} {'Mode':<8} {'Pressure':<15} {'EPR':<10}"
        )
        click.echo("=" * 80)

        for idx, period in enumerate(stats_periods):
            days_count = len(period.days)
            start_str = period.start_date.strftime("%Y-%m-%d")
            end_str = (
                period.end_date.strftime("%Y-%m-%d")
                if idx < len(stats_periods) - 1
                else "present"
            )
            date_range = f"{start_str}..{end_str}"

            mode = period.settings.get("mode", "?")[:7]
            epr = f"{period.settings.get('epr_level', '?')} {period.settings.get('epr_mode', '?')[:2]}"

            if "pressure_min" in period.settings and "pressure_max" in period.settings:
                pressure_str = f"{period.settings['pressure_min']}-{period.settings['pressure_max']}"
            elif "pressure_fixed" in period.settings:
                pressure_str = f"{period.settings['pressure_fixed']} (F)"
            else:
                pressure_str = "?"

            ahi_str = f"{period.avg_ahi:.1f}" if period.avg_ahi is not None else "N/A"
            leak_str = (
                f"{period.avg_leak:.1f}" if period.avg_leak is not None else "N/A"
            )

            marker = ""
            if best and period is best:
                marker = "  <- Best"
            elif worst and period is worst:
                marker = "  <- Worst"

            click.echo(
                f"{date_range:<25} {days_count:<6} {ahi_str:<10} {leak_str:<10} {mode:<8} {pressure_str:<15} {epr:<10}{marker}"
            )

        click.echo("=" * 80)

        if best:
            click.echo(f"\nBest Period (Avg AHI: {best.avg_ahi:.1f}):")
            click.echo(
                f"  {best.start_date.strftime('%Y-%m-%d')} to {best.end_date.strftime('%Y-%m-%d')} ({len(best.days)} days)"
            )
            click.echo(f"  Settings: {best.settings}")

        if worst:
            click.echo(f"\nWorst Period (Avg AHI: {worst.avg_ahi:.1f}):")
            click.echo(
                f"  {worst.start_date.strftime('%Y-%m-%d')} to {worst.end_date.strftime('%Y-%m-%d')} ({len(worst.days)} days)"
            )
            click.echo(f"  Settings: {worst.settings}")
