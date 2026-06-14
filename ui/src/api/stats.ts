import { apiGet, apiGetOrNull } from './client'
import type { TherapySummary, PeriodStatistics, TrendData, RecordsData } from '@/types'

export const getSummary = apiGetOrNull<TherapySummary, [daysLimit?: number]>(
    '/stats/summary',
    (daysLimit) => ({ params: daysLimit != null ? { days_limit: daysLimit } : {} }),
)

export const getPeriods = apiGet<PeriodStatistics[], [periodType?: string, daysLimit?: number]>(
    '/stats/periods',
    (periodType = 'month', daysLimit) => ({
        params: {
            period_type: periodType,
            ...(daysLimit != null ? { days_limit: daysLimit } : {}),
        },
    }),
)

export const getTrends = apiGet<TrendData, [periodType?: string, daysLimit?: number]>(
    '/stats/trends',
    (periodType = 'month', daysLimit) => ({
        params: {
            period_type: periodType,
            ...(daysLimit != null ? { days_limit: daysLimit } : {}),
        },
    }),
)

export const getRecords = apiGet<RecordsData, [daysLimit?: number, topN?: number]>(
    '/stats/records',
    (daysLimit, topN = 5) => ({
        params: { top_n: topN, ...(daysLimit != null ? { days_limit: daysLimit } : {}) },
    }),
)
