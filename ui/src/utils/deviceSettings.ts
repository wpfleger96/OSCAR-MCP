/**
 * Utilities for displaying device therapy settings.
 *
 * Key names mirror STR_SETTINGS_MAP in src/snore/parsers/resmed_edf.py
 * (search for STR_SETTINGS_MAP), which is the canonical source of truth for
 * stored setting keys.
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
            'ipap',
            'epap',
            'ps',
            'min_epap',
            'max_epap',
            'min_ps',
            'max_ps',
            'epap_auto',
            'ramp_start_pressure',
            'epr_level',
            'epr_mode',
        ],
    },
    {
        label: 'Comfort',
        keys: [
            'response',
            'ramp_enabled',
            'ramp_time',
            'smart_ramp',
            'ti_min',
            'ti_max',
            'rise_time',
            'trigger',
            'cycle',
            'humidity_enabled',
            'humidity_level',
            'climate_control',
            'tube_temp_enabled',
            'tube_temp',
        ],
    },
    {
        label: 'Other',
        keys: [
            'smart_start',
            'smart_stop',
            'ab_filter',
            'mask_type',
            'easy_breathe',
            'tube',
            'pt_access',
            'pt_view',
        ],
    },
]

const KNOWN_KEYS = new Set(SETTING_CATEGORIES.flatMap((c) => c.keys))

const SETTING_LABELS: Record<string, string> = {
    mode: 'Mode',
    pressure_fixed: 'Pressure',
    pressure_min: 'Min Pressure',
    pressure_max: 'Max Pressure',
    ipap: 'IPAP',
    epap: 'EPAP',
    ps: 'Pressure Support',
    min_epap: 'Min EPAP',
    max_epap: 'Max EPAP',
    min_ps: 'Min PS',
    max_ps: 'Max PS',
    epap_auto: 'EPAP Auto',
    ramp_start_pressure: 'Ramp Start Pressure',
    epr_level: 'EPR Level',
    epr_mode: 'EPR Mode',
    response: 'Response',
    ramp_enabled: 'Ramp',
    ramp_time: 'Ramp Time',
    smart_ramp: 'Smart Ramp',
    ti_min: 'Ti Min',
    ti_max: 'Ti Max',
    rise_time: 'Rise Time',
    trigger: 'Trigger',
    cycle: 'Cycle',
    humidity_enabled: 'Humidity',
    humidity_level: 'Humidity Level',
    climate_control: 'Climate Control',
    tube_temp_enabled: 'Heated Tube',
    tube_temp: 'Tube Temperature',
    smart_start: 'Smart Start',
    smart_stop: 'Smart Stop',
    ab_filter: 'Filter Type',
    mask_type: 'Mask Type',
    easy_breathe: 'Easy-Breathe',
    tube: 'Tube',
    pt_access: 'Patient Access',
    pt_view: 'Patient View',
}

const BOOLEAN_KEYS = new Set([
    'ramp_enabled',
    'humidity_enabled',
    'tube_temp_enabled',
    'smart_start',
    'smart_stop',
    'smart_ramp',
    'epap_auto',
    'easy_breathe',
])

const PRESSURE_KEYS = new Set([
    'pressure_fixed',
    'pressure_min',
    'pressure_max',
    'ramp_start_pressure',
    'ipap',
    'epap',
    'ps',
    'min_epap',
    'max_epap',
    'min_ps',
    'max_ps',
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

    if (key === 'humidity_level' && rawValue === '0') {
        return 'Off'
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
