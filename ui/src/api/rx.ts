import { apiGet, apiGetOrNull } from './client'
import type { RxChangesResponse, RxComparisonResponse, RxPeriodResponse } from '@/types'

export const getRxHistory = apiGet<RxPeriodResponse[]>('/rx/history')

export const getRxCurrent = apiGetOrNull<RxPeriodResponse>('/rx/current')

export const getRxCompare = apiGet<RxComparisonResponse, [minDays?: number]>(
    '/rx/compare',
    (minDays = 7) => ({ params: { min_days: minDays } }),
)

export const getRxChanges = apiGet<RxChangesResponse>('/rx/changes')
