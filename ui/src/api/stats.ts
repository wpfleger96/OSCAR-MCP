import api from './client'
import type { TherapySummary, PeriodStatistics, TrendData, RecordsData } from '@/types'

export async function getSummary(daysLimit?: number): Promise<TherapySummary | null> {
    const { data, status } = await api.get<TherapySummary>('/stats/summary', {
        params: daysLimit != null ? { days_limit: daysLimit } : {},
        validateStatus: (s) => s === 200 || s === 204,
    })
    return status === 204 ? null : data
}

export async function getPeriods(
    periodType: string = 'month',
    daysLimit?: number,
): Promise<PeriodStatistics[]> {
    const { data } = await api.get<PeriodStatistics[]>('/stats/periods', {
        params: {
            period_type: periodType,
            ...(daysLimit != null ? { days_limit: daysLimit } : {}),
        },
    })
    return data
}

export async function getTrends(
    periodType: string = 'month',
    daysLimit?: number,
): Promise<TrendData> {
    const { data } = await api.get<TrendData>('/stats/trends', {
        params: {
            period_type: periodType,
            ...(daysLimit != null ? { days_limit: daysLimit } : {}),
        },
    })
    return data
}

export async function getRecords(daysLimit?: number, topN: number = 5): Promise<RecordsData> {
    const { data } = await api.get<RecordsData>('/stats/records', {
        params: { top_n: topN, ...(daysLimit != null ? { days_limit: daysLimit } : {}) },
    })
    return data
}
