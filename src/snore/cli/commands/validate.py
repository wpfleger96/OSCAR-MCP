"""validate command — batch session validation against machine events."""

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
@actor_options
def validate(
    date_from: datetime,
    date_to: datetime,
    mode: str,
    export: str | None,
    db: str | None,
    actor_user: str | None,
    actor_profile: str | None,
) -> None:
    """
    Run batch validation across multiple sessions.

    Validates SNORE's detection against machine events for sessions in the specified
    date range, and displays aggregate metrics.
    """
    if date_from > date_to:
        raise click.ClickException("--from date must be before or equal to --to date")

    if db and not Path(db).expanduser().exists():
        raise click.ClickException(f"Database not found: {db}")

    async def _run() -> None:
        from snore.auth.factory import resolve_cli_profile_id  # noqa: PLC0415
        from snore.validation import (
            BatchValidator,
            export_report_csv,
            export_report_json,
        )

        async with open_db_session(db) as async_db:
            try:
                profile_id = await resolve_cli_profile_id(
                    async_db, actor_user, actor_profile
                )
                validator = BatchValidator(async_db, profile_id)

                console.print(
                    f"Running validation from {date_from.date()} to {date_to.date()}..."
                )
                console.print(f"Mode: {mode}\n")

                report = await validator.validate_date_range(
                    date_from.strftime("%Y-%m-%d"),
                    date_to.strftime("%Y-%m-%d"),
                    mode=mode,
                )

                print_footer()
                print_header("VALIDATION REPORT")
                console.print(
                    f"Date Range: {report.date_range_start} to {report.date_range_end}"
                )
                console.print(f"Sessions Analyzed: {report.aggregate.total_sessions}")
                console.print(
                    f"Total Machine Events: {report.aggregate.total_machine_events}"
                )
                console.print(
                    f"Total Programmatic Events: {report.aggregate.total_programmatic_events}"
                )

                console.print("\nAggregate Metrics:")
                console.print(
                    f"  Apneas:     "
                    f"Avg Sens: {report.aggregate.avg_apnea_sensitivity * 100:.0f}%  "
                    f"Avg Prec: {report.aggregate.avg_apnea_precision * 100:.0f}%  "
                    f"Avg F1: {report.aggregate.avg_apnea_f1:.2f}"
                )
                console.print(
                    f"  Hypopneas:  "
                    f"Avg Sens: {report.aggregate.avg_hypopnea_sensitivity * 100:.0f}%  "
                    f"Avg Prec: {report.aggregate.avg_hypopnea_precision * 100:.0f}%  "
                    f"Avg F1: {report.aggregate.avg_hypopnea_f1:.2f}"
                )

                if report.aggregate.low_sensitivity_sessions:
                    console.print(
                        f"\nSessions with Low Sensitivity (<60%): "
                        f"{len(report.aggregate.low_sensitivity_sessions)}"
                    )
                    console.print(
                        f"  Session IDs: {report.aggregate.low_sensitivity_sessions[:10]}"
                    )
                    if len(report.aggregate.low_sensitivity_sessions) > 10:
                        console.print(
                            f"  ... and {len(report.aggregate.low_sensitivity_sessions) - 10} more"
                        )

                console.print("\nPer-Session Results:")
                console.print(
                    f"{'Date':<12} {'ID':<6} {'Machine':<8} {'Prog':<8} {'Apnea Sens':<11} {'Hypopnea Sens':<13}"
                )
                print_footer()

                for session in report.sessions[:10]:
                    console.print(
                        f"{session.date:<12} "
                        f"{session.session_id:<6} "
                        f"{session.machine_event_count:<8} "
                        f"{session.programmatic_event_count:<8} "
                        f"{session.apnea_sensitivity * 100:>6.0f}%     "
                        f"{session.hypopnea_sensitivity * 100:>6.0f}%"
                    )

                if len(report.sessions) > 10:
                    console.print(f"... and {len(report.sessions) - 10} more sessions")

                if export:
                    export_path = Path(export)
                    if export_path.suffix == ".json":
                        export_report_json(report, export_path)
                        console.print(f"\nReport exported to {export_path}")
                    elif export_path.suffix == ".csv":
                        export_report_csv(report, export_path)
                        console.print(f"\nReport exported to {export_path}")
                    else:
                        raise click.ClickException(
                            f"Unknown export format '{export_path.suffix}'. Use .json or .csv"
                        )

            except click.ClickException:
                raise
            except Exception as e:
                import traceback

                err_console.print(f"Validation error: {e}")
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    traceback.print_exc()
                raise click.ClickException(str(e)) from e

    asyncio.run(_run())
