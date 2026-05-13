"""validate command — batch session validation against machine events."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click

from snore.cli.decorators import date_range_options_required, db_option, init_db


@click.command()
@date_range_options_required
@click.option(
    "--mode",
    "-m",
    default="aasm",
    type=str,
    help="Detection mode to validate (default: aasm)",
)
@click.option(
    "--export",
    type=click.Path(),
    help="Export report to file (.json or .csv)",
)
@db_option
def validate(
    date_from: datetime,
    date_to: datetime,
    mode: str,
    export: str | None,
    db: str | None,
) -> None:
    """
    Run batch validation across multiple sessions.

    Validates SNORE's detection against machine events for sessions in the specified
    date range, and displays aggregate metrics.
    """
    from snore.database.session import session_scope
    from snore.validation import BatchValidator, export_report_csv, export_report_json

    if date_from > date_to:
        raise click.ClickException("--from date must be before or equal to --to date")

    init_db(db)

    with session_scope() as db_session:
        try:
            validator = BatchValidator(db_session, None)

            click.echo(
                f"Running validation from {date_from.date()} to {date_to.date()}..."
            )
            click.echo(f"Mode: {mode}\n")

            report = validator.validate_date_range(
                date_from.strftime("%Y-%m-%d"),
                date_to.strftime("%Y-%m-%d"),
                mode=mode,
            )

            click.echo("=" * 60)
            click.echo("VALIDATION REPORT")
            click.echo("=" * 60)
            click.echo(
                f"Date Range: {report.date_range_start} to {report.date_range_end}"
            )
            click.echo(f"Sessions Analyzed: {report.aggregate.total_sessions}")
            click.echo(f"Total Machine Events: {report.aggregate.total_machine_events}")
            click.echo(
                f"Total Programmatic Events: {report.aggregate.total_programmatic_events}"
            )

            click.echo("\nAggregate Metrics:")
            click.echo(
                f"  Apneas:     "
                f"Avg Sens: {report.aggregate.avg_apnea_sensitivity * 100:.0f}%  "
                f"Avg Prec: {report.aggregate.avg_apnea_precision * 100:.0f}%  "
                f"Avg F1: {report.aggregate.avg_apnea_f1:.2f}"
            )
            click.echo(
                f"  Hypopneas:  "
                f"Avg Sens: {report.aggregate.avg_hypopnea_sensitivity * 100:.0f}%  "
                f"Avg Prec: {report.aggregate.avg_hypopnea_precision * 100:.0f}%  "
                f"Avg F1: {report.aggregate.avg_hypopnea_f1:.2f}"
            )

            if report.aggregate.low_sensitivity_sessions:
                click.echo(
                    f"\nSessions with Low Sensitivity (<60%): "
                    f"{len(report.aggregate.low_sensitivity_sessions)}"
                )
                click.echo(
                    f"  Session IDs: {report.aggregate.low_sensitivity_sessions[:10]}"
                )
                if len(report.aggregate.low_sensitivity_sessions) > 10:
                    click.echo(
                        f"  ... and {len(report.aggregate.low_sensitivity_sessions) - 10} more"
                    )

            click.echo("\nPer-Session Results:")
            click.echo(
                f"{'Date':<12} {'ID':<6} {'Machine':<8} {'Prog':<8} {'Apnea Sens':<11} {'Hypopnea Sens':<13}"
            )
            click.echo("=" * 60)

            for session in report.sessions[:10]:
                click.echo(
                    f"{session.date:<12} "
                    f"{session.session_id:<6} "
                    f"{session.machine_event_count:<8} "
                    f"{session.programmatic_event_count:<8} "
                    f"{session.apnea_sensitivity * 100:>6.0f}%     "
                    f"{session.hypopnea_sensitivity * 100:>6.0f}%"
                )

            if len(report.sessions) > 10:
                click.echo(f"... and {len(report.sessions) - 10} more sessions")

            if export:
                export_path = Path(export)
                if export_path.suffix == ".json":
                    export_report_json(report, export_path)
                    click.echo(f"\nReport exported to {export_path}")
                elif export_path.suffix == ".csv":
                    export_report_csv(report, export_path)
                    click.echo(f"\nReport exported to {export_path}")
                else:
                    raise click.ClickException(
                        f"Unknown export format '{export_path.suffix}'. Use .json or .csv"
                    )

        except click.ClickException:
            raise
        except Exception as e:
            import sys
            import traceback

            click.echo(f"Validation error: {e}", err=True)
            if "--verbose" in sys.argv or "-v" in sys.argv:
                traceback.print_exc()
            raise click.ClickException(str(e)) from e
