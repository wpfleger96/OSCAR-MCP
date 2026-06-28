import { apiGet, apiPost, apiPatch, apiDelete } from './client'
import type { PaginatedResponse, SessionListItem, SessionDetail, DeletePreview } from '@/types'

export interface SessionsParams {
    limit?: number
    offset?: number
    sort_by?: string
    include_disabled?: boolean
    from_date?: string
    to_date?: string
    device?: string
}

export const getSessions = apiGet<PaginatedResponse<SessionListItem>, [params?: SessionsParams]>(
    '/sessions/',
    (params = {}) => ({ params }),
)

export const getSession = apiGet<SessionDetail, [id: number, includeSettings?: boolean]>(
    (id) => `/sessions/${id}`,
    (_id, includeSettings = true) => ({ params: { include_settings: includeSettings } }),
)

export const updateSession = apiPatch<SessionDetail, [id: number, body: { enabled: boolean }]>(
    (id) => `/sessions/${id}`,
    (_id, body) => ({ data: body }),
)

export const deleteSessions = apiDelete<
    { deleted_count: number },
    [body: { session_ids: number[] }]
>('/sessions/', (body) => ({ data: body }))

export const getSessionDeletePreview = apiGet<DeletePreview, [sessionId: number]>(
    (sessionId) => `/sessions/${sessionId}/delete-preview`,
)

export const getBulkDeletePreview = apiPost<
    DeletePreview,
    [
        body: {
            session_ids?: number[]
            device?: string
            from_date?: string
            to_date?: string
            delete_all?: boolean
        },
    ]
>('/sessions/delete-preview', (body) => ({ data: body }))
