import { apiGet } from './client'
import type { WaveformInfo, WaveformDataResponse } from '@/types'

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
        params: { max_points: 2000, ...params },
        signal,
    }),
)
