import { apiPost } from './client'
import type { ValidationReport } from '@/types'

export interface ValidationParams {
    from_date: string
    to_date: string
    mode?: 'aasm' | 'aasm_relaxed' | 'resmed'
}

export const runValidation = apiPost<ValidationReport, [body: ValidationParams]>(
    '/validate/',
    (body) => ({ data: body }),
)
