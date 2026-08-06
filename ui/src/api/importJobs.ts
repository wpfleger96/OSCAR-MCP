import { apiGet, apiDelete } from './client'
import type { PipelineJobsListResponse } from '@/types'

export const ACTIVE_PIPELINE_STAGES: ReadonlySet<string> = new Set([
    'uploading',
    'queued',
    'importing',
    'analysis_queued',
    'analyzing',
])

export const getImportJobs = apiGet<PipelineJobsListResponse>('/import/jobs')

export const cancelImport = apiDelete<void, [jobId: string]>((jobId) => `/import/${jobId}`)
