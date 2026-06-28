import { apiGet, apiPost } from './client'
import type { DatabaseStatsPublic, VacuumResult } from '@/types'

export const getDbStats = apiGet<DatabaseStatsPublic>('/db/stats')

export const vacuumDb = apiPost<VacuumResult>('/db/vacuum')
