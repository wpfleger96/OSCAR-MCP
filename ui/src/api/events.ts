import { apiGet } from './client'
import type { EventItem, EventMatchResult } from '@/types'

export const getSessionEvents = apiGet<EventItem[], [sessionId: number, eventType?: string]>(
    (sessionId) => `/sessions/${sessionId}/events`,
    (_sessionId, eventType) => ({ params: eventType ? { event_type: eventType } : {} }),
)

export const getEventMatch = apiGet<EventMatchResult, [sessionId: number, mode?: string]>(
    (sessionId) => `/sessions/${sessionId}/events/match`,
    (_sessionId, mode = 'aasm') => ({ params: { mode } }),
)
