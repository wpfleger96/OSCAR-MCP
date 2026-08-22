import { apiPost } from './client'
import type {
    ValidationReport,
    FlValidationReport,
    BreathTrendsValidationReport,
    ReraValidationReport,
    AppleCrossValidationReport,
} from '@/types'

export interface ValidationParams {
    from_date: string
    to_date: string
    mode?: 'aasm' | 'aasm_relaxed' | 'resmed'
}

export interface DateRangeParams {
    from_date: string
    to_date: string
}

export const runValidation = apiPost<ValidationReport, [body: ValidationParams]>(
    '/validate/',
    (body) => ({ data: body }),
)

// Direct (synchronous) legacy endpoints — CLI parity. The persisted-runs flow in
// validationRuns.ts is preferred for the UI; these exist for API-client
// completeness and one-off scripted calls.
export const runFlValidation = apiPost<FlValidationReport, [body: DateRangeParams]>(
    '/validate/fl',
    (body) => ({ data: body }),
)

export const runBreathTrendsValidation = apiPost<
    BreathTrendsValidationReport,
    [body: DateRangeParams]
>('/validate/breaths', (body) => ({ data: body }))

export const runReraValidation = apiPost<ReraValidationReport, [body: DateRangeParams]>(
    '/validate/rera',
    (body) => ({ data: body }),
)

export const runAppleValidation = apiPost<AppleCrossValidationReport, [body: DateRangeParams]>(
    '/validate/apple',
    (body) => ({ data: body }),
)
