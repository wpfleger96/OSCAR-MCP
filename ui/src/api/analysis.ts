import { apiGet, apiPost, apiDelete } from './client'
import type {
    PaginatedResponse,
    AnalysisListItem,
    AnalysisResult,
    AnalysisDeletePreview,
} from '@/types'

export interface AnalysisSessionsParams {
    limit?: number
    offset?: number
    from_date?: string
    to_date?: string
    analyzed_only?: boolean
    sort_by?: string
}

export const getAnalysisSessions = apiGet<
    PaginatedResponse<AnalysisListItem>,
    [params?: AnalysisSessionsParams]
>('/analysis/sessions', (params = {}) => ({ params }))

export const getAnalysis = apiGet<AnalysisResult, [sessionId: number]>(
    (sessionId) => `/sessions/${sessionId}/analysis`,
)

export const runAnalysis = apiPost<
    AnalysisResult,
    [sessionId: number, body?: { modes?: string[]; store_results?: boolean }]
>(
    (sessionId) => `/sessions/${sessionId}/analysis`,
    (_sessionId, body = {}) => ({ data: { modes: ['aasm'], store_results: true, ...body } }),
)

export const deleteAnalysis = apiDelete<
    { deleted_count: number },
    [body: { session_ids: number[]; all_versions?: boolean }]
>('/analysis', (body) => ({ data: body }))

export const getAnalysisDeletePreview = apiGet<
    AnalysisDeletePreview,
    [params?: { session_ids?: number[]; all_versions?: boolean }]
>('/analysis/delete-preview', (params = {}) => ({ params }))

export const runBatchAnalysis = apiPost<
    { job_id: string; session_count: number },
    [
        body: {
            from_date?: string
            to_date?: string
            missing_only?: boolean
            modes?: string[]
            store_results?: boolean
        },
    ]
>('/analysis/batch', (body) => ({ data: body }))

export interface AnalysisJobInfo {
    job_id: string
    state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
    source: string
    session_count: number
    progress_completed: number
    progress_total: number
    error_message: string | null
    created_at: number
    started_at: number | null
    finished_at: number | null
    owner_user_id: number | null
}

export const getAnalysisJobs = apiGet<{ jobs: AnalysisJobInfo[] }>('/analysis/jobs')

export const cancelAnalysisJob = apiDelete<void, [jobId: string]>(
    (jobId) => `/analysis/jobs/${jobId}`,
)
