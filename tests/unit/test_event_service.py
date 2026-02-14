"""Unit tests for EventService."""

from snore.services.event_service import EVENT_MATCH_TOLERANCE_SECONDS, EventService


class TestEventService:
    """Tests for EventService.match_events()."""

    def test_empty_events(self):
        """Empty event lists return all zeros."""
        service = EventService()
        result = service.match_events([], [])

        assert result.machine_count == 0
        assert result.programmatic_count == 0
        assert result.matched == 0
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_all_matched(self):
        """Identical timestamps result in all matched, no false positives or negatives."""
        service = EventService()
        machine_times = [10.0, 20.0, 30.0]
        programmatic_times = [10.0, 20.0, 30.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 3
        assert result.programmatic_count == 3
        assert result.matched == 3
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_within_tolerance(self):
        """Events within tolerance are matched."""
        service = EventService()
        machine_times = [10.0, 20.0]
        programmatic_times = [13.0, 22.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 2
        assert result.programmatic_count == 2
        assert result.matched == 2
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_outside_tolerance(self):
        """Events outside tolerance result in false positives and false negatives."""
        service = EventService()
        machine_times = [10.0]
        programmatic_times = [16.1]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 1
        assert result.programmatic_count == 1
        assert result.matched == 0
        assert result.false_positives == 1
        assert result.false_negatives == 1

    def test_false_negatives_only(self):
        """Machine events with no programmatic events result in false negatives."""
        service = EventService()
        machine_times = [10.0, 20.0, 30.0]
        programmatic_times = []

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 3
        assert result.programmatic_count == 0
        assert result.matched == 0
        assert result.false_positives == 0
        assert result.false_negatives == 3

    def test_false_positives_only(self):
        """Programmatic events with no machine events result in false positives."""
        service = EventService()
        machine_times = []
        programmatic_times = [10.0, 20.0, 30.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 0
        assert result.programmatic_count == 3
        assert result.matched == 0
        assert result.false_positives == 3
        assert result.false_negatives == 0

    def test_mixed_match(self):
        """Mixed scenario with some matched and some unmatched events."""
        service = EventService()
        machine_times = [10.0, 20.0, 30.0, 40.0]
        programmatic_times = [11.0, 35.0, 50.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 4
        assert result.programmatic_count == 3
        assert result.matched == 3
        assert result.false_positives == 1
        assert result.false_negatives == 1

    def test_custom_tolerance(self):
        """Custom tolerance parameter allows tighter matching."""
        service = EventService()
        machine_times = [10.0, 20.0]
        programmatic_times = [12.5, 22.5]

        result_tight = service.match_events(
            machine_times, programmatic_times, tolerance=2.0
        )
        assert result_tight.matched == 0
        assert result_tight.false_positives == 2
        assert result_tight.false_negatives == 2

        result_loose = service.match_events(
            machine_times, programmatic_times, tolerance=3.0
        )
        assert result_loose.matched == 2
        assert result_loose.false_positives == 0
        assert result_loose.false_negatives == 0

    def test_unsorted_input(self):
        """Unsorted input lists are handled correctly."""
        service = EventService()
        machine_times = [30.0, 10.0, 20.0]
        programmatic_times = [20.0, 30.0, 10.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 3
        assert result.programmatic_count == 3
        assert result.matched == 3
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_default_tolerance_constant(self):
        """Default tolerance uses the module constant."""
        service = EventService()
        machine_times = [10.0]
        programmatic_times = [10.0 + EVENT_MATCH_TOLERANCE_SECONDS]

        result = service.match_events(machine_times, programmatic_times)

        assert result.matched == 1
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_edge_case_exact_tolerance_boundary(self):
        """Events exactly at tolerance boundary are matched."""
        service = EventService()
        machine_times = [10.0]
        programmatic_times = [15.0]

        result = service.match_events(machine_times, programmatic_times, tolerance=5.0)

        assert result.matched == 1
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_many_to_one_matching(self):
        """Multiple programmatic events can match to a single machine event."""
        service = EventService()
        machine_times = [10.0]
        programmatic_times = [9.0, 10.0, 11.0]

        result = service.match_events(machine_times, programmatic_times)

        assert result.machine_count == 1
        assert result.programmatic_count == 3
        assert result.matched == 1
        assert result.false_positives == 0
        assert result.false_negatives == 0
