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

// Devices
export interface DeviceInfo {
    id: number
    manufacturer: string
    model: string
    serial_number: string
}

// Delete preview
export interface DeletePreview {
    sessions: SessionListItem[]
    event_count: number
    waveform_count: number
    stats_count: number
}

// Stats
export interface TherapySummary {
    first_date: string
    last_date: string
    days_since_last: number
    total_hours: number
    avg_hours: number
    days_with_data: number
    avg_ahi: number | null
    effectiveness: string
    avg_rei: number | null
    avg_pressure: number | null
    min_pressure: number | null
    max_pressure: number | null
    avg_epap: number | null
    avg_leak: number | null
    avg_spo2: number | null
    min_spo2: number | null
    total_spo2_time_below_90: number
    avg_pulse: number | null
    avg_respiratory_rate: number | null
    avg_tidal_volume: number | null
    avg_minute_ventilation: number | null
    event_counts: EventTypeCount[]
}

export interface EventTypeCount {
    event_type: string
    count: number
    percentage: number
}

export interface PeriodStatistics {
    period_type: string
    period_start: string
    period_end: string
    days_used: number
    days_in_period: number
    avg_hours_per_day: number | null
    avg_ahi: number | null
    median_ahi: number | null
    avg_pressure: number | null
    avg_leak: number | null
    avg_spo2: number | null
    min_spo2: number | null
}

export interface TrendData {
    ahi: [string, number | null][]
    usage: [string, number | null][]
    spo2: [string, number | null][]
    leak: [string, number | null][]
}

export interface RecordsData {
    [metric: string]: { best: [string, number][]; worst: [string, number][] }
}

// Days
export interface DayListItem {
    date: string
    device_id: number
    session_count: number
    total_therapy_hours: number | null
    ahi: number | null
}

export interface DayDetail extends DayListItem {
    oai: number | null
    cai: number | null
    hi: number | null
    avg_pressure: number | null
    avg_leak: number | null
    avg_spo2: number | null
    session_ids: number[]
}

// RX
export interface RxPeriodResponse {
    settings: Record<string, string>
    start_date: string
    end_date: string
    days_count: number
    avg_ahi: number | null
    median_ahi: number | null
    avg_hours: number | null
    total_hours: number
    avg_leak: number | null
}

export interface RxComparisonResponse {
    periods: RxPeriodResponse[]
    best_index: number | null
    worst_index: number | null
}

// Analysis
export interface AnalysisListItem {
    session_id: number
    session_date: string
    duration_hours: number | null
    has_analysis: boolean
    analysis_id: number | null
}

export interface AnalysisSessionDetail {
    id: number
    start_time: string
    manufacturer: string | null
    model: string | null
    version_count: number
}

export interface AnalysisDeletePreview {
    sessions_with_analysis: number
    total_analysis_records: number
    records_to_delete: number
    patterns_count: number
    session_details: AnalysisSessionDetail[]
}

export interface ApneaEvent {
    start_time: number
    end_time: number
    duration: number
    event_type: string
    flow_reduction: number
    confidence: number
    classification_confidence: number
    baseline_flow: number
    detection_method: string
}

export interface HypopneaEvent {
    start_time: number
    end_time: number
    duration: number
    flow_reduction: number
    confidence: number
    baseline_flow: number
    has_arousal: boolean | null
    has_desaturation: boolean | null
}

export interface RERAEvent {
    start_time: number
    end_time: number
    duration: number
    obstructed_breath_count: number
    recovery_amplitude_increase_pct: number
    confidence: number
    baseline_flow: number
}

export interface AnalysisEvent {
    event_type: string
    start_time: number
    duration: number
    source: string
    confidence: number | null
    flow_reduction: number | null
    has_desaturation: boolean | null
    baseline_flow: number | null
}

export interface ModeResult {
    mode_name: string
    apneas: ApneaEvent[]
    hypopneas: HypopneaEvent[]
    reras: RERAEvent[]
    ahi: number
    rdi: number
    metadata: Record<string, unknown>
}

export interface AnalysisResult {
    session_id: number
    session_duration_hours: number
    total_breaths: number
    machine_events: AnalysisEvent[]
    mode_results: Record<string, ModeResult>
    flow_analysis: Record<string, unknown> | null
    csr_detection: Record<string, unknown> | null
    periodic_breathing: Record<string, unknown> | null
    csr_episodes: Record<string, unknown>[] | null
    periodic_breathing_episodes: Record<string, unknown>[] | null
    pulse_change_count: number | null
    pulse_change_index: number | null
    timestamp_start: number
    timestamp_end: number
}

export interface EventMatchResult {
    machine_count: number
    programmatic_count: number
    matched: number
    false_positives: number
    false_negatives: number
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
