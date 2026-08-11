import { apiDelete, apiGet, apiPatch, apiPost } from './client'
import type { MaskLogEntryResponse } from '@/types'
import type { components } from '@/types/generated'

type MaskLogCreateRequest = components['schemas']['MaskLogCreateRequest']
type MaskLogUpdateRequest = components['schemas']['MaskLogUpdateRequest']

export const getMaskLog = apiGet<MaskLogEntryResponse[]>('/equipment/masks')

export const createMaskLogEntry = apiPost<MaskLogEntryResponse, [body: MaskLogCreateRequest]>(
    '/equipment/masks',
    (body) => ({ data: body }),
)

export const updateMaskLogEntry = apiPatch<
    MaskLogEntryResponse,
    [entryId: number, body: MaskLogUpdateRequest]
>(
    (entryId) => `/equipment/masks/${entryId}`,
    (_entryId, body) => ({ data: body }),
)

export const deleteMaskLogEntry = apiDelete<void, [entryId: number]>(
    (entryId) => `/equipment/masks/${entryId}`,
)
