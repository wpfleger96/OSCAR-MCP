import { apiGet } from './client'
import type {
    PaginatedResponse,
    HealthNightSummaryRead,
    HealthNightDetailRead,
    HealthSampleRead,
    DateListResponse,
} from '@/types'

export interface HealthNightsParams {
    limit?: number
    offset?: number
    from_date?: string
    to_date?: string
}

export const getHealthNights = apiGet<
    PaginatedResponse<HealthNightSummaryRead>,
    [params?: HealthNightsParams]
>('/health/nights', (params = {}) => ({ params }))

export const getHealthNightDates = apiGet<DateListResponse>('/health/nights/dates')

export const getHealthNight = apiGet<HealthNightDetailRead, [date: string]>(
    (date) => `/health/nights/${date}`,
)

export const getHealthNightSamples = apiGet<
    HealthSampleRead[],
    [date: string, sourceName?: string]
>(
    (date) => `/health/nights/${date}/samples`,
    (_date, sourceName) => ({ params: sourceName ? { source_name: sourceName } : {} }),
)
