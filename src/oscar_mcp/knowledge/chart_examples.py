"""
OSCAR Chart and Graph Reference Images

Reference images showing standard OSCAR data visualization for different
channels and metrics. Useful for:
- Understanding chart interpretation
- Teaching users how to read OSCAR data
- Multimodal LLM analysis (comparing user charts with examples)
- Documentation and user guides
"""

from typing import Any

# Waveform and Time-Series Charts
# These show continuous data over time
WAVEFORM_CHARTS = {
    "flow_rate": {
        "name": "Flow Rate Chart",
        "channel": "CPAP_FlowRate",
        "description": "Respiratory flow rate waveform showing inspiration and expiration",
        "unit": "L/min",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_flow_rate_graph_example.png",
            "data/guidelines/images/charts/OSCAR_flow_rate_detail_view.png",
        ],
        "key_features": [
            "Positive values = inhalation",
            "Negative values = exhalation",
            "Waveform shape indicates flow limitation class",
            "Amplitude relates to tidal volume",
        ],
        "interpretation_tips": [
            "Look for flattening (flow limitation)",
            "Check for regular breathing pattern",
            "Identify apneas (flat sections)",
        ],
    },
    "pressure": {
        "name": "Pressure Chart",
        "channel": "CPAP_Pressure",
        "description": "Therapy pressure over time",
        "unit": "cm H2O",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_pressure_graph.png",
            "data/guidelines/images/charts/OSCAR_pressure_graph_detailed.png",
        ],
        "key_features": [
            "Shows CPAP/APAP pressure adjustments",
            "May show EPR (expiratory pressure relief) patterns",
            "Pressure changes correlate with events",
        ],
        "interpretation_tips": [
            "APAP: Pressure increases indicate detected events",
            "CPAP: Should be relatively flat",
            "EPR visible as pressure drops during exhalation",
        ],
    },
    "leak_rate": {
        "name": "Leak Rate Chart",
        "channel": "CPAP_Leak",
        "description": "Mask leak rate over time",
        "unit": "L/min",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_leak_rate_graph_resmed.png",
            "data/guidelines/images/charts/OSCAR_leak_rate_graph_phillips.png",
        ],
        "key_features": [
            "Manufacturer-specific reporting",
            "ResMed: Total leak (includes intentional)",
            "Philips: Unintentional leak only",
        ],
        "interpretation_tips": [
            "ResMed: <24 L/min is acceptable",
            "Philips: <24 L/min is acceptable",
            "Spikes indicate mask displacement",
            "Sustained high leak reduces therapy efficacy",
        ],
        "manufacturer_differences": True,
    },
    "mask_pressure": {
        "name": "Mask Pressure Chart",
        "channel": "CPAP_MaskPressure",
        "description": "Pressure measured at the mask (ResMed ASV/VPAP)",
        "unit": "cm H2O",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_mask_pressure_example_resmed_asv.png",
        ],
        "key_features": [
            "Only available on certain devices",
            "Shows actual delivered pressure",
            "May differ from target pressure due to leaks",
        ],
    },
    "snore_rate": {
        "name": "Snore Rate Graph",
        "channel": "CPAP_Snore",
        "description": "Snoring vibration detection (ResMed devices)",
        "unit": "index",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_snore_rate_graph_resmed.png",
        ],
        "key_features": [
            "Acoustic vibration detection",
            "Higher values = more snoring",
            "May indicate partial obstruction",
        ],
        "interpretation_tips": [
            "Snoring often precedes flow limitation",
            "Elevated snore index suggests inadequate pressure",
        ],
    },
}


# Summary Graphs and Histograms
# These show aggregated or statistical data
SUMMARY_GRAPHS = {
    "time_at_pressure": {
        "name": "Time at Pressure Histogram",
        "description": "Distribution of time spent at each pressure level",
        "reference_images": [
            "data/guidelines/images/charts/OSCAR_time_at_pressure_graph.png",
            "data/guidelines/images/charts/OSCAR_timte_at_pressure_graph_APAP.png",
        ],
        "device_types": ["APAP", "Auto-CPAP"],
        "key_features": [
            "Histogram showing pressure distribution",
            "X-axis: Pressure (cm H2O)",
            "Y-axis: Time spent at each pressure",
            "Shows how much pressure variation occurred",
        ],
        "interpretation_tips": [
            "Wide distribution = APAP working hard to find optimal pressure",
            "Narrow distribution = relatively stable breathing",
            "Peak shows most common pressure level",
            "Useful for setting CPAP pressure from APAP data",
        ],
    },
    "daily_ahi": {
        "name": "Daily AHI Graph",
        "description": "AHI trends over multiple days/weeks",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_daily_AHI_graph.png",
        ],
        "key_features": [
            "Shows AHI improvement over time",
            "Identifies problematic nights",
            "Tracks therapy effectiveness",
        ],
    },
}


# UI Reference Screenshots
# OSCAR user interface examples for documentation
UI_REFERENCE = {
    "daily_screen": {
        "name": "OSCAR Daily Screen",
        "description": "Main daily data view showing all channels and events",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_daily_screen.png",
            "data/guidelines/images/ui/OSCAR_daily_screen_left_sidebar_details.png",
        ],
        "components": [
            "Left sidebar with statistics",
            "Channel graphs (flow, pressure, leak, etc.)",
            "Event markers and flags",
            "Time navigation",
        ],
    },
    "events_breakdown": {
        "name": "Events Breakdown Tab",
        "description": "Detailed view of respiratory events",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_daily_screen_events_breakdown_tab.png",
            "data/guidelines/images/ui/OSCAR_daily_screen_event_breakdown_pie_chart.png",
        ],
    },
    "bookmarks": {
        "name": "Bookmarks Breakdown",
        "description": "User bookmarks and annotations",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_daily_screen_bookmarks_breakdown_tab.png",
        ],
    },
    "event_flags": {
        "name": "Event Flags Chart",
        "description": "Visual markers showing respiratory events on timeline",
        "reference_images": [
            "data/guidelines/images/events/OSCAR_daily_standard_charts_event_flags_chart.png",
        ],
        "key_features": [
            "Color-coded event markers",
            "OA, CA, H, RERA, FL indicators",
            "Temporal distribution of events",
        ],
    },
    "settings": {
        "name": "Custom Event Flagging Settings",
        "description": "Configuration for event detection and flagging",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_custom_event_flagging_settings.png",
        ],
    },
    "calendar_details": {
        "name": "Calendar Details View",
        "description": "Monthly calendar view with daily statistics",
        "reference_images": [
            "data/guidelines/images/ui/OCAR_daily_screen_calendar_details.png",
        ],
    },
    "sidebar_summary": {
        "name": "Sidebar Summary Statistics",
        "description": "Aggregated statistics and compliance info",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_sidebar_summary.png",
        ],
    },
    "graph_controls": {
        "name": "Graph Y-Axis Controls",
        "description": "How to adjust graph scale and zoom",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_daily_screen_detailed_graphs_how_to_change_y_axis.png",
        ],
    },
    "optional_integrations": {
        "name": "Optional Data Imports",
        "description": "Integration with other devices (e.g., Zeo sleep monitor)",
        "reference_images": [
            "data/guidelines/images/ui/OSCAR_optional_zeo_data_import.png",
        ],
    },
}


# Chart Example Categories for Easy Access
CHART_CATEGORIES = {
    "waveforms": WAVEFORM_CHARTS,
    "summaries": SUMMARY_GRAPHS,
    "ui": UI_REFERENCE,
}


def get_chart_image(category: str, chart_name: str, image_index: int = 0) -> str | None:
    """
    Get reference image path for a specific chart.

    Args:
        category: Chart category ("waveforms", "summaries", "ui")
        chart_name: Name of the chart
        image_index: Index of image if multiple available (default 0)

    Returns:
        Path to reference image

    Example:
        >>> get_chart_image("waveforms", "flow_rate")
        'data/guidelines/images/charts/OSCAR_flow_rate_graph_example.png'
    """
    if category not in CHART_CATEGORIES:
        raise ValueError(f"Unknown category: {category}")

    category_charts: dict[str, Any] = CHART_CATEGORIES[category]  # type: ignore[assignment]
    if chart_name not in category_charts:
        raise ValueError(f"Unknown chart: {chart_name}")

    images = category_charts[chart_name].get("reference_images", [])
    if not images:
        return None

    if image_index >= len(images):
        image_index = 0

    return images[image_index]


def list_all_chart_images() -> list:
    """
    Get list of all chart reference images.

    Returns:
        List of image paths
    """
    images = []
    for category_charts in CHART_CATEGORIES.values():
        charts_dict: dict[str, Any] = category_charts  # type: ignore[assignment]
        for chart in charts_dict.values():
            images.extend(chart.get("reference_images", []))
    return images
