"""Human-readable labels and value formatting for device therapy settings.

Mirrors ui/src/utils/deviceSettings.ts — both files carry the same key labels
and value-formatting rules. The TS file cross-references the parser's
STR_SETTINGS_MAP; this module is the CLI counterpart.
"""

from __future__ import annotations

# Keys that carry a boolean "true"/"false" string in the DB and should be
# displayed as On/Off.  Matches the BOOLEAN_KEYS set in deviceSettings.ts.
_BOOLEAN_KEYS: frozenset[str] = frozenset(
    {
        "ramp_enabled",
        "humidity_enabled",
        "tube_temp_enabled",
        "smart_start",
        "smart_stop",
        "smart_ramp",
        "epap_auto",
        "easy_breathe",
    }
)

# Keys whose numeric value represents a pressure in cmH₂O.
# Matches PRESSURE_KEYS in deviceSettings.ts.
_PRESSURE_KEYS: frozenset[str] = frozenset(
    {
        "pressure_fixed",
        "pressure_min",
        "pressure_max",
        "ramp_start_pressure",
        "ipap",
        "epap",
        "ps",
        "min_epap",
        "max_epap",
        "min_ps",
        "max_ps",
    }
)

# Human-readable labels for stored setting keys.  Mirrors SETTING_LABELS in
# deviceSettings.ts, with the addition of pt_view.
SETTING_KEY_LABELS: dict[str, str] = {
    "mode": "Mode",
    "pressure_fixed": "Pressure",
    "pressure_min": "Min Pressure",
    "pressure_max": "Max Pressure",
    "ipap": "IPAP",
    "epap": "EPAP",
    "ps": "Pressure Support",
    "min_epap": "Min EPAP",
    "max_epap": "Max EPAP",
    "min_ps": "Min PS",
    "max_ps": "Max PS",
    "epap_auto": "EPAP Auto",
    "ramp_start_pressure": "Ramp Start Pressure",
    "epr_level": "EPR Level",
    "epr_mode": "EPR Mode",
    "response": "Response",
    "ramp_enabled": "Ramp",
    "ramp_time": "Ramp Time",
    "smart_ramp": "Smart Ramp",
    "ti_min": "Ti Min",
    "ti_max": "Ti Max",
    "rise_time": "Rise Time",
    "trigger": "Trigger",
    "cycle": "Cycle",
    "humidity_enabled": "Humidity",
    "humidity_level": "Humidity Level",
    "climate_control": "Climate Control",
    "tube_temp_enabled": "Heated Tube",
    "tube_temp": "Tube Temperature",
    "smart_start": "Smart Start",
    "smart_stop": "Smart Stop",
    "ab_filter": "Filter Type",
    "mask_type": "Mask Type",
    "easy_breathe": "Easy-Breathe",
    "tube": "Tube",
    "pt_access": "Patient Access",
    "pt_view": "Patient View",
}


def format_setting_key(key: str) -> str:
    """Return the human-readable label for a setting key, falling back to the key itself."""
    return SETTING_KEY_LABELS.get(key, key)


def format_setting_value(key: str, value: str) -> str:
    """Return a display-ready string for a stored setting value.

    Formatting rules mirror deviceSettings.ts formatSettingValue:
    - Boolean keys → "On" / "Off"
    - Pressure keys → "<n.1f> cmH2O"
    - ramp_time → "<n> min"
    - tube_temp → °C converted to °F, formatted as "<n.1f>°F"
    - humidity_level "0" → "Off"
    - Everything else passes through unchanged.
    """
    if key in _BOOLEAN_KEYS:
        lower = value.lower()
        return "On" if lower in ("true", "1") else "Off"

    if key in _PRESSURE_KEYS:
        try:
            n = float(value)
            return f"{n:.1f} cmH2O"
        except ValueError:
            return value

    if key == "ramp_time":
        try:
            n = int(value)
            return f"{n} min"
        except ValueError:
            return value

    if key == "tube_temp":
        try:
            c = float(value)
            f = (c * 9) / 5 + 32
            return f"{f:.1f}°F"
        except ValueError:
            return value

    if key == "humidity_level" and value == "0":
        return "Off"

    return value
