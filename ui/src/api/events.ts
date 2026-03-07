import api from './client'
import type { EventItem } from '@/types'

export async function getSessionEvents(
    sessionId: number,
    eventType?: string,
): Promise<EventItem[]> {
    const { data } = await api.get<EventItem[]>(`/sessions/${sessionId}/events`, {
        params: eventType ? { event_type: eventType } : {},
    })
    return data
}
