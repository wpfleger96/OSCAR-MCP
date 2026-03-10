import api from './client'
import type {
    PaginatedResponse,
    AnalysisListItem,
    AnalysisResult,
    AnalysisDeletePreview,
} from '@/types'

export async function getAnalysisSessions(
    params: {
        limit?: number
        offset?: number
        from_date?: string
        to_date?: string
        analyzed_only?: boolean
        sort_by?: string
    } = {},
): Promise<PaginatedResponse<AnalysisListItem>> {
    const { data } = await api.get<PaginatedResponse<AnalysisListItem>>('/analysis/sessions', {
        params,
    })
    return data
}

export async function getAnalysis(sessionId: number): Promise<AnalysisResult> {
    const { data } = await api.get<AnalysisResult>(`/sessions/${sessionId}/analysis`)
    return data
}

export async function runAnalysis(
    sessionId: number,
    body: { modes?: string[]; store_results?: boolean } = {},
): Promise<AnalysisResult> {
    const { data } = await api.post<AnalysisResult>(`/sessions/${sessionId}/analysis`, {
        modes: ['aasm'],
        store_results: true,
        ...body,
    })
    return data
}

export async function deleteAnalysis(body: {
    session_ids: number[]
    all_versions?: boolean
}): Promise<{ deleted_count: number }> {
    const { data } = await api.delete<{ deleted_count: number }>('/analysis', { data: body })
    return data
}

export async function getAnalysisDeletePreview(
    params: { session_ids?: number[]; all_versions?: boolean } = {},
): Promise<AnalysisDeletePreview> {
    const { data } = await api.get<AnalysisDeletePreview>('/analysis/delete-preview', { params })
    return data
}
