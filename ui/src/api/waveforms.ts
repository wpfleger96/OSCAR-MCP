import api from './client'
import type { WaveformInfo, WaveformDataResponse } from '@/types'

export async function getWaveformTypes(sessionId: number): Promise<WaveformInfo[]> {
    const { data } = await api.get<WaveformInfo[]>(`/sessions/${sessionId}/waveforms`)
    return data
}

export async function getWaveformData(
    sessionId: number,
    waveformType: string,
    params: {
        max_points?: number
        start_seconds?: number
        end_seconds?: number
    } = {},
    signal?: AbortSignal,
): Promise<WaveformDataResponse> {
    const { data } = await api.get<WaveformDataResponse>(
        `/sessions/${sessionId}/waveforms/${waveformType}`,
        { params: { max_points: 2000, ...params }, signal },
    )
    return data
}
