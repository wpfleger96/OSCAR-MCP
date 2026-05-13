"""stats command — therapy usage and clinical statistics."""

from __future__ import annotations

from typing import Literal, cast

import click

from snore.cli.decorators import date_range_options, db_option, init_db


@click.command()
@db_option
@date_range_options
@click.option("--days", type=int, help="Limit to last N days")
@click.option(
    "--period",
    type=click.Choice(["week", "month", "6month", "year"]),
    help="Show statistics broken down by period",
)
@click.option("--trend", is_flag=True, help="Show trend analysis chart")
@click.option(
    "--records", is_flag=True, help="Show top 5 best/worst days for key metrics"
)
def stats(
    db: str | None,
    date_from: None,
    date_to: None,
    days: int | None,
    period: str | None,
    trend: bool,
    records: bool,
) -> None:
    """Show therapy usage and clinical statistics."""
    from snore.database.session import session_scope
    from snore.services.schemas import PeriodStatistics
    from snore.services.stats_service import StatsService

    if trend and not period:
        period = "week"

    init_db(db)

    with session_scope() as session:
        service = StatsService(session)
        summary = service.get_summary(days)

        if not summary:
            click.echo("\n📈 Therapy Statistics")
            click.echo(f"{'=' * 60}")
            click.echo("\nNo therapy data found.")
            click.echo(f"{'=' * 60}\n")
            return

        click.echo("\n📈 Therapy Statistics")
        click.echo(f"{'=' * 60}")

        click.echo("\nDate Range")
        click.echo(f"  First session: {summary.first_date}")
        click.echo(f"  Last session: {summary.last_date}")
        click.echo(f"  Days since last use: {summary.days_since_last}")

        click.echo("\nUsage")
        click.echo(f"  Total therapy hours: {summary.total_hours:,.1f} hrs")
        click.echo(f"  Average per night: {summary.avg_hours:.1f} hrs")
        click.echo(f"  Days with data: {summary.days_with_data}")

        click.echo("\nClinical")
        if summary.avg_ahi is not None:
            click.echo(f"  Average AHI: {summary.avg_ahi:.1f}")
        else:
            click.echo("  Average AHI: N/A")
        click.echo(f"  Effectiveness: {summary.effectiveness}")

        if summary.avg_rei is not None:
            click.echo(f"  Average REI: {summary.avg_rei:.1f}")

        if summary.avg_pressure is not None:
            click.echo("\nPressure")
            click.echo(f"  Average: {summary.avg_pressure:.1f} cmH₂O")
            if summary.min_pressure is not None and summary.max_pressure is not None:
                click.echo(
                    f"  Range: {summary.min_pressure:.1f} - {summary.max_pressure:.1f} cmH₂O"
                )

        if summary.avg_epap is not None:
            click.echo("\nEPAP")
            click.echo(f"  Average: {summary.avg_epap:.1f} cmH₂O")

        if summary.avg_leak is not None:
            click.echo("\nLeak")
            click.echo(f"  Average: {summary.avg_leak:.1f} L/min")
            leak_assessment = "well controlled" if summary.avg_leak < 24 else "elevated"
            click.echo(f"  Assessment: {leak_assessment}")

        if summary.avg_spo2 is not None:
            click.echo("\nSpO₂")
            click.echo(f"  Average: {summary.avg_spo2:.1f}%")
            if summary.min_spo2 is not None:
                click.echo(f"  Minimum recorded: {summary.min_spo2:.0f}%")

        if summary.total_spo2_time_below_90 > 0:
            minutes_below_90 = summary.total_spo2_time_below_90 / 60
            click.echo(f"  Time below 90%: {minutes_below_90:.1f} minutes")

        if summary.avg_pulse is not None:
            click.echo("\nPulse")
            click.echo(f"  Average: {summary.avg_pulse:.1f} BPM")

        if (
            summary.avg_respiratory_rate is not None
            or summary.avg_tidal_volume is not None
            or summary.avg_minute_ventilation is not None
        ):
            click.echo("\nRespiratory")
            if summary.avg_respiratory_rate is not None:
                click.echo(
                    f"  Respiratory Rate: {summary.avg_respiratory_rate:.1f} breaths/min"
                )
            if summary.avg_tidal_volume is not None:
                click.echo(f"  Tidal Volume: {summary.avg_tidal_volume:.0f} mL")
            if summary.avg_minute_ventilation is not None:
                click.echo(
                    f"  Minute Ventilation: {summary.avg_minute_ventilation:.1f} L/min"
                )

        if summary.event_counts:
            click.echo("\nEvents")
            for ec in summary.event_counts:
                click.echo(f"  {ec.event_type}: {ec.count:,} ({ec.percentage:.1f}%)")

        if period:
            period_literal = cast(Literal["week", "month", "6month", "year"], period)
            period_stats: list[PeriodStatistics] = service.get_period_statistics(
                period_literal, days
            )

            if period_stats:
                period_names = {
                    "week": "Weekly",
                    "month": "Monthly",
                    "6month": "6-Month",
                    "year": "Yearly",
                }

                click.echo(f"\n\nTherapy Statistics ({period_names[period]})")
                click.echo(f"{'=' * 80}")

                click.echo(
                    f"{'Period':<20} {'Days':<6} {'Avg Hours':<11} {'Avg AHI':<9} {'Med AHI':<9}"
                )
                click.echo("-" * 80)

                for period_stat in period_stats:  # type: PeriodStatistics
                    if period == "week":
                        period_label = f"{period_stat.period_start.strftime('%Y-W%U')}"
                    elif period == "month":
                        period_label = period_stat.period_start.strftime("%b %Y")
                    elif period == "6month":
                        half = "H1" if period_stat.period_start.month == 1 else "H2"
                        period_label = f"{period_stat.period_start.year} {half}"
                    else:
                        period_label = str(period_stat.period_start.year)

                    days_str = f"{period_stat.days_used}/{period_stat.days_in_period}"

                    hours_str = (
                        f"{period_stat.avg_hours_per_day:.1f}h"
                        if period_stat.avg_hours_per_day is not None
                        else "N/A"
                    )

                    avg_ahi_str = (
                        f"{period_stat.avg_ahi:.1f}"
                        if period_stat.avg_ahi is not None
                        else "N/A"
                    )

                    med_ahi_str = (
                        f"{period_stat.median_ahi:.1f}"
                        if period_stat.median_ahi is not None
                        else "N/A"
                    )

                    click.echo(
                        f"{period_label:<20} {days_str:<6} {hours_str:<11} {avg_ahi_str:<9} {med_ahi_str:<9}"
                    )

                click.echo("=" * 80)

                if trend:
                    import plotext as plt

                    trends = service.get_trends(period_stats)
                    ahi_trend = trends["ahi"]

                    ahi_values = [v for _, v in ahi_trend if v is not None]
                    if ahi_values:
                        dates_for_plot = [d for d, v in ahi_trend if v is not None]
                        date_labels = [d.strftime("%Y-%m-%d") for d in dates_for_plot]
                        x_indices = list(range(len(ahi_values)))

                        latest_ahi = ahi_values[-1]
                        if len(ahi_values) > 1:
                            prior_avg = sum(ahi_values[:-1]) / len(ahi_values[:-1])
                            if latest_ahi < prior_avg * 0.9:
                                direction = "(improving)"
                            elif latest_ahi > prior_avg * 1.1:
                                direction = "(worsening)"
                            else:
                                direction = "(stable)"
                        else:
                            direction = ""

                        click.echo("\n\nAHI Trend")
                        click.echo("=" * 80)

                        plt.clf()
                        plt.plot(x_indices, ahi_values, marker="braille")
                        plt.xticks(x_indices, date_labels)
                        plt.title(f"AHI Over Time {direction}")
                        plt.xlabel("Period")
                        plt.ylabel("AHI (events/hour)")
                        plt.show()

                        click.echo("=" * 80)

        if records:
            records_data = service.get_records(days, top_n=5)

            if records_data:
                click.echo("\n\nRecords (Top 5)")
                click.echo("=" * 80)

                metric_labels = {
                    "ahi": ("Best AHI", "Worst AHI"),
                    "leak": ("Best Leak", "Worst Leak"),
                    "therapy_hours": ("Longest Sessions", "Shortest Sessions"),
                    "spo2_min": ("Best SpO2 Min", "Worst SpO2 Min"),
                }

                for metric, (best_label, worst_label) in metric_labels.items():
                    if metric not in records_data:
                        continue

                    best_records = records_data[metric]["best"]
                    worst_records = records_data[metric]["worst"]

                    click.echo(f"\n{best_label:<35} {worst_label}")
                    click.echo("-" * 80)

                    max_rows = max(len(best_records), len(worst_records))
                    for i in range(max_rows):
                        best_str = ""
                        worst_str = ""

                        if i < len(best_records):
                            dt, val = best_records[i]
                            if metric == "therapy_hours":
                                best_str = f"  {dt}: {val:.1f}h"
                            else:
                                best_str = f"  {dt}: {val:.1f}"

                        if i < len(worst_records):
                            dt, val = worst_records[i]
                            if metric == "therapy_hours":
                                worst_str = f"{dt}: {val:.1f}h"
                            else:
                                worst_str = f"{dt}: {val:.1f}"

                        click.echo(f"{best_str:<35} {worst_str}")

                click.echo("=" * 80)

        click.echo(f"\n{'=' * 60}\n")
