"""Unit tests for ResMed parse_sessions limit semantics.

`limit` must bound the number of *yielded sessions*, not the number of nights
parsed. A night can be dropped (per-session date filter, or a parse failure), so
truncating the night list up front would under-deliver.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from snore.parsers.resmed_edf import ResmedEDFParser


def _fake_session(night_date: str) -> MagicMock:
    session = MagicMock()
    session.start_time = datetime.fromisoformat(
        f"{night_date[:4]}-{night_date[4:6]}-{night_date[6:8]}T22:00:00"
    )
    return session


def test_limit_counts_yielded_sessions_not_nights_parallel():
    """A dropped early night must not reduce the yielded count below `limit`."""
    parser = ResmedEDFParser()
    # 3-tuples: (night_date, chain_id, segments)
    nights = [(f"2024010{i}", f"2024010{i}_000000", {}) for i in range(1, 5)]

    def fake_bundle(
        night_date: str, chain_id: str, *args: object, **kwargs: object
    ) -> MagicMock | None:
        # First night yields nothing (e.g. filtered out or failed to parse).
        if night_date == "20240101":
            return None
        return _fake_session(night_date)

    with (
        patch.object(
            parser, "_discover_session_files", return_value=(Path("/data"), nights)
        ),
        patch.object(
            parser, "_filter_night_items", side_effect=lambda items, *a: items
        ),
        patch.object(parser, "_load_str_caches", return_value=({}, {})),
        patch.object(parser, "get_device_info", return_value=MagicMock()),
        patch(
            "snore.parsers.base.get_pool",
            return_value=ThreadPoolExecutor(max_workers=4),
        ),
        patch(
            "snore.parsers.resmed_edf._resmed_parse_bundle_worker",
            side_effect=fake_bundle,
        ),
    ):
        result = list(parser.parse_sessions(Path("/data"), limit=2, parallel=True))

    # Pre-truncating to night_items[:2] would parse only nights 1-2; night 1
    # yields None, leaving a single session. The limit must instead be applied
    # to yielded sessions, reaching across to a later valid night.
    assert len(result) == 2
