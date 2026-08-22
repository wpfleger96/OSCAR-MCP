import { apiGet } from './client'
import { BASE_MAX_POINTS } from '@/constants/waveform'
import type { WaveformInfo, WaveformDataResponse, EventComparisonResult } from '@/types'

export interface WaveformDataParams {
    max_points?: number
    start_seconds?: number
    end_seconds?: number
}

export const getWaveformTypes = apiGet<WaveformInfo[], [sessionId: number]>(
    (sessionId) => `/sessions/${sessionId}/waveforms`,
)

export const getWaveformData = apiGet<
    WaveformDataResponse,
    [sessionId: number, waveformType: string, params?: WaveformDataParams, signal?: AbortSignal]
>(
    (sessionId, waveformType) => `/sessions/${sessionId}/waveforms/${waveformType}`,
    (_sessionId, _waveformType, params = {}, signal) => ({
        params: { max_points: BASE_MAX_POINTS, ...params },
        signal,
    }),
)

export const getWaveformCompare = apiGet<
    EventComparisonResult,
    [sessionId: number, params?: { mode?: string }]
>(
    (sessionId) => `/sessions/${sessionId}/waveforms/compare`,
    (_sessionId, params = {}) => ({ params }),
)
