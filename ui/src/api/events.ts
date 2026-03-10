import api from './client'
import type { EventItem, EventMatchResult } from '@/types'

export async function getSessionEvents(
    sessionId: number,
    eventType?: string,
): Promise<EventItem[]> {
    const { data } = await api.get<EventItem[]>(`/sessions/${sessionId}/events`, {
        params: eventType ? { event_type: eventType } : {},
    })
    return data
}

export async function getEventMatch(
    sessionId: number,
    mode: string = 'aasm',
): Promise<EventMatchResult> {
    const { data } = await api.get<EventMatchResult>(`/sessions/${sessionId}/events/match`, {
        params: { mode },
    })
    return data
}
