import { apiGet, apiGetOrNull } from './client'
import type { RxPeriodResponse, RxComparisonResponse } from '@/types'

export const getRxHistory = apiGet<RxPeriodResponse[]>('/rx/history')

export const getRxCurrent = apiGetOrNull<RxPeriodResponse>('/rx/current')

export const getRxCompare = apiGet<RxComparisonResponse, [minDays?: number]>(
    '/rx/compare',
    (minDays = 7) => ({ params: { min_days: minDays } }),
)
