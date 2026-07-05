import { apiGet, apiPost } from './client'
import type { DatabaseStatsPublic, VacuumResult, ResetResult } from '@/types'

export const getDbStats = apiGet<DatabaseStatsPublic>('/db/stats')
export const vacuumDb = apiPost<VacuumResult>('/db/vacuum')
export const resetDb = apiPost<ResetResult>('/db/reset')
