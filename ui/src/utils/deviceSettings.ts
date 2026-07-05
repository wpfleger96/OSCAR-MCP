/**
 * Utilities for displaying device therapy settings.
 *
 * Key names mirror STR_SETTINGS_MAP in src/snore/parsers/resmed_edf.py (lines 82-101),
 * which is the canonical source of truth for stored setting keys.
 */

export interface SettingsCategory {
    label: string
    keys: string[]
}

export const SETTING_CATEGORIES: SettingsCategory[] = [
    {
        label: 'Therapy',
        keys: [
            'mode',
            'pressure_fixed',
            'pressure_min',
            'pressure_max',
            'ramp_start_pressure',
            'epr_level',
            'epr_mode',
        ],
    },
    {
        label: 'Comfort',
        keys: [
            'ramp_enabled',
            'ramp_time',
            'humidity_enabled',
            'humidity_level',
            'climate_control',
            'tube_temp_enabled',
            'tube_temp',
        ],
    },
    {
        label: 'Other',
        keys: ['smart_start', 'ab_filter', 'mask_type'],
    },
]

const KNOWN_KEYS = new Set(SETTING_CATEGORIES.flatMap((c) => c.keys))

const SETTING_LABELS: Record<string, string> = {
    mode: 'Mode',
    pressure_fixed: 'Pressure',
    pressure_min: 'Min Pressure',
    pressure_max: 'Max Pressure',
    ramp_start_pressure: 'Ramp Start Pressure',
    epr_level: 'EPR Level',
    epr_mode: 'EPR Mode',
    ramp_enabled: 'Ramp',
    ramp_time: 'Ramp Time',
    humidity_enabled: 'Humidity',
    humidity_level: 'Humidity Level',
    climate_control: 'Climate Control',
    tube_temp_enabled: 'Heated Tube',
    tube_temp: 'Tube Temperature',
    smart_start: 'Smart Start',
    ab_filter: 'Filter Type',
    mask_type: 'Mask Type',
}

const BOOLEAN_KEYS = new Set([
    'ramp_enabled',
    'humidity_enabled',
    'tube_temp_enabled',
    'smart_start',
])

const PRESSURE_KEYS = new Set([
    'pressure_fixed',
    'pressure_min',
    'pressure_max',
    'ramp_start_pressure',
])

/** Human-readable label for a setting key. */
export function settingLabel(key: string): string {
    return SETTING_LABELS[key] ?? key
}

/** Format a stored setting value for display. */
export function formatSettingValue(key: string, rawValue: string): string {
    if (BOOLEAN_KEYS.has(key)) {
        const lower = rawValue.toLowerCase()
        return lower === 'true' || lower === '1' ? 'On' : 'Off'
    }

    if (PRESSURE_KEYS.has(key)) {
        const n = parseFloat(rawValue)
        return Number.isFinite(n) ? `${n.toFixed(1)} cmH₂O` : rawValue
    }

    if (key === 'ramp_time') {
        const n = parseInt(rawValue, 10)
        return Number.isFinite(n) ? `${n} min` : rawValue
    }

    if (key === 'tube_temp') {
        const c = parseFloat(rawValue)
        if (Number.isFinite(c)) {
            const f = (c * 9) / 5 + 32
            return `${f.toFixed(1)}°F`
        }
        return rawValue
    }

    return rawValue
}

/** Partition a flat settings dict into categorized groups + an "Other settings" bucket. */
export function categorizeSettings(settings: Record<string, string>): {
    categories: { label: string; entries: { key: string; label: string; value: string }[] }[]
    other: { key: string; label: string; value: string }[]
} {
    const categories = SETTING_CATEGORIES.map((cat) => ({
        label: cat.label,
        entries: cat.keys
            .filter((k) => k in settings)
            .map((k) => ({
                key: k,
                label: settingLabel(k),
                value: formatSettingValue(k, settings[k]),
            })),
    })).filter((cat) => cat.entries.length > 0)

    const other = Object.entries(settings)
        .filter(([k]) => !KNOWN_KEYS.has(k))
        .map(([k, v]) => ({ key: k, label: settingLabel(k), value: v }))

    return { categories, other }
}
