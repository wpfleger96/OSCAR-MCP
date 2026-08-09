import { apiGet, apiPost } from './client'
import type { DatabaseStatsPublic, VacuumResult, ResetResult } from '@/types'

export interface ResetRequest {
    include_accounts?: boolean
}

export const getDbStats = apiGet<DatabaseStatsPublic>('/db/stats')
export const vacuumDb = apiPost<VacuumResult>('/db/vacuum')
export const resetDb = apiPost<ResetResult, [body?: ResetRequest]>('/db/reset', (body) =>
    body ? { data: body } : {},
)
