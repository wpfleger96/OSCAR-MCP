import api from './client'
import type { PaginatedResponse, DayListItem, DayDetail } from '@/types'

export async function getDays(
    params: {
        limit?: number
        offset?: number
        from_date?: string
        to_date?: string
        device_id?: number
    } = {},
): Promise<PaginatedResponse<DayListItem>> {
    const { data } = await api.get<PaginatedResponse<DayListItem>>('/days/', { params })
    return data
}

export async function getDay(date: string): Promise<DayDetail> {
    const { data } = await api.get<DayDetail>(`/days/${date}`)
    return data
}
