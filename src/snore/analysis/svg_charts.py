"""Pure-Python SVG chart renderer for therapy reports.

All functions return a self-contained ``<svg>`` element string with no
third-party dependencies.  They never raise regardless of input.
"""

import itertools
import math

from datetime import date

# ---------------------------------------------------------------------------
# Plot-area padding constants (px)
# ---------------------------------------------------------------------------
PAD_LEFT: int = 45
PAD_RIGHT: int = 15
PAD_TOP: int = 10
PAD_BOTTOM: int = 30

_Y_TICKS: int = 4
_MAX_X_LABELS: int = 8


def _escape(text: str) -> str:
    """Minimally escape XML special characters for use in text content.

    Args:
        text: Raw string to escape.

    Returns:
        String with ``&``, ``<``, and ``>`` replaced by XML entities.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    """Return n evenly-spaced values from lo to hi inclusive.

    Args:
        lo: Start value.
        hi: End value.
        n: Number of points (must be >= 1).

    Returns:
        List of n floats spanning [lo, hi].
    """
    if n <= 1:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


def _svg_open(width: int, height: int) -> str:
    """Return the opening ``<svg>`` tag with viewBox and dimensions.

    Args:
        width: Canvas width in pixels.
        height: Canvas height in pixels.

    Returns:
        Opening SVG tag string.
    """
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
    )


def _axis_lines(width: int, height: int) -> str:
    """Return left and bottom axis line elements.

    Args:
        width: Canvas width in pixels.
        height: Canvas height in pixels.

    Returns:
        Two ``<line>`` elements forming the L-shaped axes.
    """
    left_x = PAD_LEFT
    top_y = PAD_TOP
    bottom_y = height - PAD_BOTTOM
    right_x = width - PAD_RIGHT
    return (
        f'<line x1="{left_x}" y1="{top_y}" x2="{left_x}" y2="{bottom_y}" '
        f'stroke="#9ca3af" stroke-width="1"/>'
        f'<line x1="{left_x}" y1="{bottom_y}" x2="{right_x}" y2="{bottom_y}" '
        f'stroke="#9ca3af" stroke-width="1"/>'
    )


def render_trend_line(
    series: list[tuple[date, float | None]],
    *,
    width: int = 600,
    height: int = 120,
    color: str = "#2563eb",
    y_label: str = "",
) -> str:
    """Render a trend line chart as a self-contained SVG element.

    None values in the series split the line into separate ``<polyline>``
    segments.  Isolated single-point segments render as a filled circle so
    they remain visible.

    Args:
        series: Sequence of (date, value) pairs; None values create line gaps.
        width: SVG canvas width in pixels.
        height: SVG canvas height in pixels.
        color: Stroke and fill color for lines and point markers.
        y_label: Optional rotated label drawn beside the y-axis.

    Returns:
        A complete ``<svg>`` element string with no XML preamble.
    """
    parts: list[str] = [_svg_open(width, height)]

    plot_w = width - PAD_LEFT - PAD_RIGHT
    plot_h = height - PAD_TOP - PAD_BOTTOM

    valid_pairs = [(d, v) for d, v in series if v is not None]

    if not valid_pairs:
        parts.append(_axis_lines(width, height))
        parts.append("</svg>")
        return "".join(parts)

    n = len(series)
    x_scale = plot_w / (n - 1) if n > 1 else 0.0

    def x_of(i: int) -> float:
        return PAD_LEFT + i * x_scale

    y_vals = [v for _, v in valid_pairs]
    y_min = min(y_vals)
    y_max = max(y_vals)
    y_range = y_max - y_min

    def y_of(v: float) -> float:
        if y_range == 0.0:
            return PAD_TOP + plot_h / 2.0
        return PAD_TOP + plot_h * (1.0 - (v - y_min) / y_range)

    # Y-axis gridlines and tick labels
    for tick in _linspace(y_min, y_max, _Y_TICKS):
        yp = y_of(tick)
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{yp:.1f}" x2="{width - PAD_RIGHT}" '
            f'y2="{yp:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 4}" y="{yp:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" font-size="9" fill="#6b7280">{tick:.1f}</text>'
        )

    # Optional rotated y-axis label
    if y_label:
        lx = float(PAD_LEFT - 36)
        ly = PAD_TOP + plot_h / 2.0
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="9" '
            f'fill="#6b7280" transform="rotate(-90,{lx:.1f},{ly:.1f})">'
            f"{_escape(y_label)}</text>"
        )

    parts.append(_axis_lines(width, height))

    # X-axis date labels
    dates = [d for d, _ in series]
    date_span = (dates[-1] - dates[0]).days if len(dates) > 1 else 0
    date_fmt = "%b %d" if date_span <= 60 else "%b %Y"
    x_step = max(1, math.ceil(n / _MAX_X_LABELS))
    label_indices: list[int] = list(range(0, n, x_step))
    if n - 1 not in label_indices:
        label_indices.append(n - 1)
    for i in label_indices:
        parts.append(
            f'<text x="{x_of(i):.1f}" y="{height - PAD_BOTTOM + 12}" '
            f'text-anchor="middle" font-size="9" fill="#6b7280">'
            f"{_escape(dates[i].strftime(date_fmt))}</text>"
        )

    # Line segments — None values split into separate polylines
    for is_valid, grp in itertools.groupby(
        enumerate(series), key=lambda t: t[1][1] is not None
    ):
        if not is_valid:
            continue
        pts = list(grp)

        if len(pts) == 1:
            idx, (_, val) = pts[0]
            if val is not None:
                parts.append(
                    f'<circle cx="{x_of(idx):.1f}" cy="{y_of(val):.1f}" '
                    f'r="3" fill="{color}"/>'
                )
        else:
            coords = " ".join(
                f"{x_of(idx):.1f},{y_of(val):.1f}"
                for idx, (_, val) in pts
                if val is not None
            )
            parts.append(
                f'<polyline points="{coords}" fill="none" stroke="{color}" '
                f'stroke-width="2"/>'
            )

    parts.append("</svg>")
    return "".join(parts)


def render_bar_chart(
    labels: list[str],
    values: list[float | None],
    *,
    width: int = 600,
    height: int = 100,
    color: str = "#2563eb",
) -> str:
    """Render a bar chart as a self-contained SVG element.

    Bars extend from a zero baseline.  None values are skipped — no
    ``<rect>`` is emitted for that position.

    Args:
        labels: X-axis label for each bar position.
        values: Y value for each bar; None skips the bar but not the label.
        width: SVG canvas width in pixels.
        height: SVG canvas height in pixels.
        color: Fill color for bars.

    Returns:
        A complete ``<svg>`` element string with no XML preamble.
    """
    parts: list[str] = [_svg_open(width, height)]

    plot_w = width - PAD_LEFT - PAD_RIGHT
    plot_h = height - PAD_TOP - PAD_BOTTOM

    n = len(labels)
    valid_vals = [v for v in values if v is not None]

    if n == 0 or not valid_vals:
        parts.append(_axis_lines(width, height))
        parts.append("</svg>")
        return "".join(parts)

    # Always include 0 in the y range so bars have a zero baseline
    y_min = min(0.0, min(valid_vals))
    y_max = max(0.0, max(valid_vals))
    y_range = y_max - y_min

    def y_of(v: float) -> float:
        if y_range == 0.0:
            return PAD_TOP + plot_h / 2.0
        return PAD_TOP + plot_h * (1.0 - (v - y_min) / y_range)

    zero_y = y_of(0.0)

    # Y-axis gridlines and tick labels
    for tick in _linspace(y_min, y_max, _Y_TICKS):
        yp = y_of(tick)
        parts.append(
            f'<line x1="{PAD_LEFT}" y1="{yp:.1f}" x2="{width - PAD_RIGHT}" '
            f'y2="{yp:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_LEFT - 4}" y="{yp:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" font-size="9" fill="#6b7280">{tick:.1f}</text>'
        )

    parts.append(_axis_lines(width, height))

    bar_slot_w = plot_w / n
    bar_w = bar_slot_w * 0.9
    x_step = max(1, math.ceil(n / _MAX_X_LABELS))

    for i, (label, value) in enumerate(zip(labels, values, strict=False)):
        bar_cx = PAD_LEFT + (i + 0.5) * bar_slot_w
        bar_left = bar_cx - bar_w / 2.0

        if value is not None:
            top_y = y_of(value)
            bar_h = abs(zero_y - top_y)
            rect_y = min(top_y, zero_y)
            parts.append(
                f'<rect x="{bar_left:.1f}" y="{rect_y:.1f}" width="{bar_w:.1f}" '
                f'height="{bar_h:.1f}" fill="{color}"/>'
            )

        if i % x_step == 0:
            parts.append(
                f'<text x="{bar_cx:.1f}" y="{height - PAD_BOTTOM + 12}" '
                f'text-anchor="middle" font-size="9" fill="#6b7280">'
                f"{_escape(label)}</text>"
            )

    parts.append("</svg>")
    return "".join(parts)
