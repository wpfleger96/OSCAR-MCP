import { apiGet } from './client'
import type { PaginatedResponse, DayListItem, DayDetail } from '@/types'

export interface DaysParams {
    limit?: number
    offset?: number
    from_date?: string
    to_date?: string
    device_id?: number
}

export const getDays = apiGet<PaginatedResponse<DayListItem>, [params?: DaysParams]>(
    '/days/',
    (params = {}) => ({ params }),
)

export const getDay = apiGet<DayDetail, [date: string]>((date) => `/days/${date}`)

export const getDates = apiGet<{ dates: string[] }>('/days/dates')
