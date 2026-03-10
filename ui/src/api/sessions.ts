import api from './client'
import type { PaginatedResponse, SessionListItem, SessionDetail, DeletePreview } from '@/types'

export async function getSessions(
    params: {
        limit?: number
        offset?: number
        sort_by?: string
        include_disabled?: boolean
        from_date?: string
        to_date?: string
        device?: string
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

export async function updateSession(
    id: number,
    body: { enabled: boolean },
): Promise<SessionDetail> {
    const { data } = await api.patch<SessionDetail>(`/sessions/${id}`, body)
    return data
}

export async function deleteSessions(body: {
    session_ids: number[]
}): Promise<{ deleted_count: number }> {
    const { data } = await api.delete<{ deleted_count: number }>('/sessions/', { data: body })
    return data
}

export async function getSessionDeletePreview(sessionId: number): Promise<DeletePreview> {
    const { data } = await api.get<DeletePreview>(`/sessions/${sessionId}/delete-preview`)
    return data
}
