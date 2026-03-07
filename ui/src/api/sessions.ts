import api from './client'
import type { PaginatedResponse, SessionListItem, SessionDetail } from '@/types'

export async function getSessions(
    params: {
        limit?: number
        offset?: number
        sort_by?: string
        include_disabled?: boolean
    } = {},
): Promise<PaginatedResponse<SessionListItem>> {
    const { data } = await api.get<PaginatedResponse<SessionListItem>>('/sessions/', { params })
    return data
}

export async function getSession(id: number, includeSettings = true): Promise<SessionDetail> {
    const { data } = await api.get<SessionDetail>(`/sessions/${id}`, {
        params: { include_settings: includeSettings },
    })
    return data
}
