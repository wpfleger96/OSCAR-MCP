"""
Tests for the pure-Python SVG chart renderer.

Validates structural correctness (well-formed XML), edge-case resilience,
and rendering spec compliance for both chart types.
"""

import xml.etree.ElementTree as ET

from datetime import date, timedelta

from snore.analysis.svg_charts import render_bar_chart, render_trend_line

SVG_NS = "http://www.w3.org/2000/svg"


def _parse(svg: str) -> ET.Element:
    """Parse SVG string into an ElementTree element."""
    return ET.fromstring(svg)


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    """Return all direct and nested descendants with the given local tag."""
    return root.findall(f".//{{{SVG_NS}}}{tag}") + root.findall(f".//{tag}")


# ---------------------------------------------------------------------------
# render_trend_line — structural and basic output
# ---------------------------------------------------------------------------


class TestRenderTrendLineOutput:
    """SVG output structure for trend line charts."""

    def test_output_starts_with_svg_tag(self) -> None:
        """Output string begins with an opening <svg tag."""
        series = [(date(2024, 1, 1), 1.0), (date(2024, 1, 2), 2.0)]
        result = render_trend_line(series)
        assert result.startswith("<svg")

    def test_output_is_valid_xml(self) -> None:
        """Output is well-formed XML that ElementTree can parse."""
        series = [(date(2024, 1, 1), 1.0), (date(2024, 1, 2), 2.0)]
        result = render_trend_line(series)
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")

    def test_none_gap_yields_two_polylines(self) -> None:
        """A None value in the middle splits the line into exactly 2 polylines."""
        series = [
            (date(2024, 1, 1), 1.0),
            (date(2024, 1, 2), 1.5),
            (date(2024, 1, 3), None),
            (date(2024, 1, 4), 3.0),
            (date(2024, 1, 5), 3.5),
        ]
        result = render_trend_line(series)
        root = _parse(result)
        polylines = _find_all(root, "polyline")
        assert len(polylines) == 2

    def test_empty_series_valid_svg_no_polylines(self) -> None:
        """Empty series produces valid SVG with no polyline elements."""
        result = render_trend_line([])
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")
        assert _find_all(root, "polyline") == []

    def test_all_none_series_valid_svg_no_exception(self) -> None:
        """All-None series produces valid SVG without raising."""
        series = [(date(2024, 1, 1), None), (date(2024, 1, 2), None)]
        result = render_trend_line(series)
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")
        assert _find_all(root, "polyline") == []

    def test_single_point_valid_svg_with_circle_marker(self) -> None:
        """Single data point produces valid SVG with a circle point marker."""
        series = [(date(2024, 1, 1), 5.0)]
        result = render_trend_line(series)
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")
        circles = _find_all(root, "circle")
        assert len(circles) >= 1

    def test_flat_series_valid_svg_no_exception(self) -> None:
        """Series where all values are identical produces valid SVG (no div-by-zero)."""
        series = [
            (date(2024, 1, 1), 7.0),
            (date(2024, 1, 2), 7.0),
            (date(2024, 1, 3), 7.0),
        ]
        result = render_trend_line(series)
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")

    def test_negative_values_valid_svg(self) -> None:
        """Series with negative values produces valid SVG without raising."""
        series = [
            (date(2024, 1, 1), -3.0),
            (date(2024, 1, 2), -1.0),
            (date(2024, 1, 3), 2.0),
        ]
        result = render_trend_line(series)
        _parse(result)  # must not raise

    def test_single_none_surrounded_by_values_yields_two_circles(self) -> None:
        """Isolated values on each side of a gap become circle markers, not polylines."""
        series = [
            (date(2024, 1, 1), 2.0),
            (date(2024, 1, 2), None),
            (date(2024, 1, 3), 5.0),
        ]
        result = render_trend_line(series)
        root = _parse(result)
        # Each isolated point renders as a circle, not a polyline
        assert _find_all(root, "polyline") == []
        assert len(_find_all(root, "circle")) == 2


# ---------------------------------------------------------------------------
# render_trend_line — date label format
# ---------------------------------------------------------------------------


class TestRenderTrendLineDateLabels:
    """X-axis label formatting based on date range."""

    def test_short_range_uses_day_level_format(self) -> None:
        """Date range ≤60 days formats labels as 'Mon DD' (e.g. 'Jan 01')."""
        start = date(2024, 1, 1)
        series = [(start + timedelta(days=i), float(i)) for i in range(30)]
        result = render_trend_line(series)
        # '%b %d' produces labels like 'Jan 01'; '%b %Y' produces 'Jan 2024'
        # A 4-digit year in a label would only appear with %b %Y
        assert "2024" not in result or "Jan 01" in result or "Jan 1" in result

    def test_exactly_60_days_uses_day_level_format(self) -> None:
        """Date range exactly 60 days still formats labels as 'Mon DD'."""
        start = date(2024, 1, 1)
        series = [(start + timedelta(days=i), float(i + 1)) for i in range(61)]
        result = render_trend_line(series)
        root = _parse(result)
        texts = [el.text or "" for el in _find_all(root, "text")]
        # At least one label should contain a comma-less month abbreviation + day
        day_style = any(
            any(
                month in t
                for month in (
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                )
            )
            and len(t) <= 6  # 'Jan 01' = 6 chars
            for t in texts
        )
        assert day_style

    def test_long_range_uses_month_year_format(self) -> None:
        """Date range >60 days formats labels as 'Mon YYYY' (e.g. 'Jan 2024')."""
        start = date(2024, 1, 1)
        series = [(start + timedelta(days=i * 5), float(i)) for i in range(20)]
        # 19 * 5 = 95 days > 60
        result = render_trend_line(series)
        root = _parse(result)
        texts = [el.text or "" for el in _find_all(root, "text")]
        # '%b %Y' labels contain 4-digit year
        assert any("2024" in t for t in texts)


# ---------------------------------------------------------------------------
# render_trend_line — y_label
# ---------------------------------------------------------------------------


class TestRenderTrendLineYLabel:
    """Y-axis label rendering."""

    def test_y_label_appears_when_provided(self) -> None:
        """Provided y_label text appears somewhere in the SVG output."""
        series = [(date(2024, 1, 1), 1.0), (date(2024, 1, 2), 2.0)]
        result = render_trend_line(series, y_label="AHI")
        assert "AHI" in result

    def test_y_label_is_xml_escaped(self) -> None:
        """y_label containing '&' is properly escaped to '&amp;' in the output."""
        series = [(date(2024, 1, 1), 1.0), (date(2024, 1, 2), 2.0)]
        result = render_trend_line(series, y_label="events/hr & more")
        assert "&amp;" in result
        assert "events/hr &amp; more" in result
        # Raw '&' must not appear outside an entity reference
        # (The SVG entity references we emit are &amp; and numeric — none contain
        # a literal bare '&' once we strip the entities we inserted.)
        _parse(result)  # would raise ParseError if ill-formed

    def test_no_y_label_when_empty_string(self) -> None:
        """Empty y_label produces no rotated text element for the axis label."""
        series = [(date(2024, 1, 1), 1.0), (date(2024, 1, 2), 2.0)]
        result = render_trend_line(series, y_label="")
        root = _parse(result)
        # The y_label element is uniquely identified by a rotate() transform.
        rotated = [
            el
            for el in _find_all(root, "text")
            if "rotate" in (el.get("transform") or "")
        ]
        assert rotated == []


# ---------------------------------------------------------------------------
# render_bar_chart — structural and basic output
# ---------------------------------------------------------------------------


class TestRenderBarChartOutput:
    """SVG output structure for bar charts."""

    def test_output_starts_with_svg_tag(self) -> None:
        """Output string begins with an opening <svg tag."""
        result = render_bar_chart(["A", "B", "C"], [1.0, 2.0, 3.0])
        assert result.startswith("<svg")

    def test_output_is_valid_xml(self) -> None:
        """Output is well-formed XML that ElementTree can parse."""
        result = render_bar_chart(["A", "B", "C"], [1.0, 2.0, 3.0])
        root = _parse(result)
        assert root.tag in ("svg", f"{{{SVG_NS}}}svg")

    def test_one_rect_per_non_none_value(self) -> None:
        """Exactly one <rect> is emitted for each non-None value."""
        labels = ["A", "B", "C", "D"]
        values: list[float | None] = [1.0, None, 3.0, None]
        result = render_bar_chart(labels, values)
        root = _parse(result)
        rects = _find_all(root, "rect")
        assert len(rects) == 2

    def test_empty_labels_valid_svg_no_rects(self) -> None:
        """Empty labels list produces valid SVG with no rect elements."""
        result = render_bar_chart([], [])
        root = _parse(result)
        assert _find_all(root, "rect") == []

    def test_all_none_values_valid_svg_no_rects(self) -> None:
        """All-None values produce valid SVG with no rect elements."""
        result = render_bar_chart(["A", "B"], [None, None])
        root = _parse(result)
        assert _find_all(root, "rect") == []

    def test_negative_values_valid_svg(self) -> None:
        """Negative bar values produce valid SVG without raising."""
        result = render_bar_chart(["A", "B", "C"], [-2.0, 0.0, 3.0])
        _parse(result)

    def test_flat_values_valid_svg_no_exception(self) -> None:
        """All-same values (including all-zero) produce valid SVG (no div-by-zero)."""
        result = render_bar_chart(["A", "B", "C"], [0.0, 0.0, 0.0])
        _parse(result)
