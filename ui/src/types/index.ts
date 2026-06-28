// API types are re-exported from the OpenAPI-generated definitions in
// ./generated.ts (regenerate with `just ui-generate-types`). Only types
// without a backend response model are hand-written below.
import type { components } from './generated'

type Schemas = components['schemas']

// Pagination — generic over the concrete instantiations FastAPI generates
// (PaginatedResponse_SessionListItem_ etc.), so the envelope shape cannot drift.
export type PaginatedResponse<T> = Omit<Schemas['PaginatedResponse_SessionListItem_'], 'items'> & {
    items: T[]
}

// Sessions
export type SessionListItem = Schemas['SessionListItem']
export type SessionStatistics = Schemas['SessionStatistics']
export type SessionSetting = Schemas['SessionSetting']
export type SessionDetail = Schemas['SessionDetail']

// Waveforms
export type WaveformInfo = Schemas['WaveformInfo']
export type WaveformDataResponse = Schemas['WaveformDataResponse']

// Events
export type EventItem = Schemas['EventItem']
export type EventMatchResult = Schemas['EventMatchResult']

// Devices
export type DeviceInfo = Schemas['DeviceInfo']

// Delete preview
export type DeletePreview = Schemas['DeletePreview']

// Stats
export type TherapySummary = Schemas['TherapySummary']
export type EventTypeCount = Schemas['EventTypeCount']
export type PeriodStatistics = Schemas['PeriodStatistics']

// Days
export type DayListItem = Schemas['DayListItem']
export type DayDetail = Schemas['DayDetail']

// RX
export type RxPeriodResponse = Schemas['RxPeriodResponse']
export type RxComparisonResponse = Schemas['RxComparisonResponse']

// Analysis
export type AnalysisListItem = Schemas['AnalysisListItem']
export type AnalysisSessionDetail = Schemas['AnalysisSessionDetail']
export type AnalysisDeletePreview = Schemas['AnalysisDeletePreview']
export type AnalysisResult = Schemas['AnalysisResult']
export type AnalysisEvent = Schemas['AnalysisEvent']
export type ModeResult = Schemas['ModeResult']
export type ApneaEvent = Schemas['ApneaEvent']
export type HypopneaEvent = Schemas['HypopneaEvent']
export type RERAEvent = Schemas['RERAEvent']

// Import
export type ImportSource = Schemas['ImportSource']
export type ImportSourceResult = Schemas['ImportSourceResult']
export type ImportResult = Schemas['ImportResult']
export type DetectRequest = Schemas['DetectRequest']

// Database
export type DatabaseStatsPublic = Schemas['DatabaseStatsPublic']
export type VacuumResult = Schemas['VacuumResult']

// Validation
export type ValidationReport = Schemas['ValidationReport']
export type AggregateMetrics = Schemas['AggregateMetrics']
export type SessionValidation = Schemas['SessionValidation']

// Batch analysis
export type BatchAnalysisRequest = Schemas['BatchAnalysisRequest']
export type BatchAnalysisResult = Schemas['BatchAnalysisResult']
export type BatchSessionResult = Schemas['BatchSessionResult']

// Waveform compare
export type EventComparisonResult = Schemas['EventComparisonResult']
export type EventComparisonDetail = Schemas['EventComparisonDetail']

// Bulk delete
export type BulkDeletePreviewRequest = Schemas['BulkDeletePreviewRequest']

// ---------------------------------------------------------------------------
// UI-only types below: /stats/trends and /stats/records return loose dict
// schemas that generate less useful TypeScript than hand-written types.
// WaveformType, WAVEFORM_LABELS, EVENT_COLORS are pure UI constants.
// ---------------------------------------------------------------------------

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

// Stats trend/records payloads (untyped on the backend)
export interface TrendData {
    ahi: [string, number | null][]
    usage: [string, number | null][]
    spo2: [string, number | null][]
    leak: [string, number | null][]
}

export interface RecordsData {
    [metric: string]: { best: [string, number][]; worst: [string, number][] }
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
