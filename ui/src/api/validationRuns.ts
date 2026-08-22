import { apiGet, apiPost, apiDelete } from './client'
import type {
    ValidatorType,
    ValidationRunStatus,
    ValidationRunDetail,
    ValidationRunsListResponse,
} from '@/types'

export interface CreateValidationRunBody {
    validator_type: ValidatorType
    from_date: string
    to_date: string
    params?: Record<string, unknown>
    // Force a fresh run even when a matching succeeded run already exists.
    force?: boolean
}

/** Enqueue a validation run (or, for `apple`, compute it synchronously).
 *  Returns the run status: `reused: true` when an existing run was returned;
 *  `state: 'succeeded'` with a null `job_id` for the synchronous path. */
export const createValidationRun = apiPost<ValidationRunStatus, [body: CreateValidationRunBody]>(
    '/validate/runs',
    (body) => ({ data: body }),
)

export interface ListValidationRunsParams {
    validator_type?: ValidatorType
    limit?: number
    offset?: number
}

export const listValidationRuns = apiGet<
    ValidationRunsListResponse,
    [params?: ListValidationRunsParams]
>('/validate/runs', (params = {}) => ({ params }))

export const getValidationRun = apiGet<ValidationRunDetail, [runId: number]>(
    (runId) => `/validate/runs/${runId}`,
)

/** Cancel a still-running run, or delete a terminal one. */
export const deleteValidationRun = apiDelete<void, [runId: number]>(
    (runId) => `/validate/runs/${runId}`,
)
