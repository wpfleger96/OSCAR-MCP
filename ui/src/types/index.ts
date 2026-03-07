// Pagination
export interface PaginatedResponse<T> {
    items: T[]
    total: number
    limit: number
    offset: number
}

// Sessions
export interface SessionListItem {
    id: number
    start_time: string
    duration_hours: number
    enabled: boolean
    manufacturer: string
    model: string
    serial_number: string
    ahi: number | null
}

export interface SessionStatistics {
    usage_hours: number | null
    ahi: number | null
    rei: number | null
    oai: number | null
    cai: number | null
    hi: number | null
    obstructive_apneas: number | null
    central_apneas: number | null
    mixed_apneas: number | null
    hypopneas: number | null
    reras: number | null
    flow_limitations: number | null
    pressure_mean: number | null
    pressure_min: number | null
    pressure_max: number | null
    pressure_95th: number | null
    epap_mean: number | null
    epap_min: number | null
    epap_max: number | null
    epap_95th: number | null
    leak_mean: number | null
    leak_percentile_70: number | null
    leak_95th: number | null
    spo2_mean: number | null
    spo2_min: number | null
    spo2_time_below_90: number | null
    pulse_mean: number | null
    pulse_min: number | null
    pulse_max: number | null
    respiratory_rate_mean: number | null
    tidal_volume_mean: number | null
    minute_ventilation_mean: number | null
}

export interface SessionSetting {
    key: string
    value: string | null
}

export interface SessionDetail {
    id: number
    device_session_id: string
    device_manufacturer: string | null
    device_model: string | null
    device_serial: string | null
    start_time: string
    end_time: string
    duration_hours: number
    duration_seconds: number
    therapy_mode: string | null
    enabled: boolean
    event_count: number
    waveform_count: number
    waveform_types: string[]
    has_statistics: boolean
    has_event_data: boolean
    statistics: SessionStatistics | null
    settings: SessionSetting[] | null
}

// Waveforms
export interface WaveformInfo {
    waveform_type: string
    sample_rate: number
    sample_count: number
    unit: string | null
    duration_hours: number
}

export interface WaveformDataResponse {
    timestamps: number[]
    values: number[]
    sample_rate: number
    unit: string
    total_samples: number
    downsampled: boolean
    returned_samples: number
}

// Events
export interface EventItem {
    id: number
    event_type: string
    start_time: number
    duration_seconds: number
    offset_seconds: number
}

// Waveform type and display labels
export type WaveformType =
    | 'flow'
    | 'pressure'
    | 'therapy_pressure'
    | 'epap'
    | 'leak'
    | 'mv'
    | 'rr'
    | 'tv'
    | 'spo2'
    | 'pulse'
    | 'fl'
    | 'snore'

export const WAVEFORM_LABELS: Record<string, string> = {
    flow: 'Flow Rate',
    pressure: 'Pressure',
    therapy_pressure: 'Therapy Pressure',
    epap: 'EPAP',
    leak: 'Leak Rate',
    mv: 'Minute Ventilation',
    rr: 'Respiratory Rate',
    tv: 'Tidal Volume',
    spo2: 'SpO₂',
    pulse: 'Pulse Rate',
    fl: 'Flow Limitation',
    snore: 'Snore',
}

// Event colors for waveform overlay
export const EVENT_COLORS: Record<string, string> = {
    OA: 'rgba(220, 38, 38, 0.25)', // red — Obstructive Apnea
    CA: 'rgba(37, 99, 235, 0.25)', // blue — Central Apnea
    MA: 'rgba(168, 85, 247, 0.25)', // purple — Mixed Apnea
    H: 'rgba(234, 179, 8, 0.25)', // yellow — Hypopnea
    RE: 'rgba(34, 197, 94, 0.25)', // green — RERA
    FL: 'rgba(249, 115, 22, 0.2)', // orange — Flow Limitation
}
