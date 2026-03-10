import api from './client'
import type { RxPeriodResponse, RxComparisonResponse } from '@/types'

export async function getRxHistory(): Promise<RxPeriodResponse[]> {
    const { data } = await api.get<RxPeriodResponse[]>('/rx/history')
    return data
}

export async function getRxCurrent(): Promise<RxPeriodResponse | null> {
    const { data, status } = await api.get<RxPeriodResponse>('/rx/current', {
        validateStatus: (s) => s === 200 || s === 204,
    })
    return status === 204 ? null : data
}

export async function getRxCompare(minDays: number = 7): Promise<RxComparisonResponse> {
    const { data } = await api.get<RxComparisonResponse>('/rx/compare', {
        params: { min_days: minDays },
    })
    return data
}
