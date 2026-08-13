import { ref, computed } from 'vue'
import { CalendarDate } from '@internationalized/date'
import type { DateValue } from 'reka-ui'
import { getDates } from '@/api/days'
import { getDataRange } from '@/api/stats'

const availableDates = ref<Set<string>>(new Set())
const minDateStr = ref<string | null>(null)
const maxDateStr = ref<string | null>(null)
const loaded = ref(false)
const loading = ref(false)

export function strToCalendarDate(s: string): CalendarDate {
    const [y, m, d] = s.split('-').map(Number)
    return new CalendarDate(y, m, d)
}

export function calendarDateToStr(d: DateValue): string {
    return `${d.year}-${String(d.month).padStart(2, '0')}-${String(d.day).padStart(2, '0')}`
}

/**
 * Returns the entries immediately before and after `current` in `sorted`.
 * `sorted` must be an ascending list of ISO date strings (as returned by the
 * API and stored in availableDates). If `current` is absent or `sorted` is
 * empty, both neighbors are null so nav arrows disable.
 */
export function adjacentDates(
    sorted: string[],
    current: string,
): { prev: string | null; next: string | null } {
    const idx = sorted.indexOf(current)
    if (idx === -1) return { prev: null, next: null }
    return {
        prev: idx > 0 ? sorted[idx - 1] : null,
        next: idx < sorted.length - 1 ? sorted[idx + 1] : null,
    }
}

export function useAvailableDates() {
    async function load(): Promise<void> {
        if (loaded.value || loading.value) return
        loading.value = true
        try {
            const [datesRes, rangeRes] = await Promise.all([getDates(), getDataRange()])
            availableDates.value = new Set(datesRes.dates)
            minDateStr.value = rangeRes.earliest_date ?? null
            maxDateStr.value = rangeRes.latest_date ?? null
            loaded.value = true
        } catch {
            // permissive fallback — dates unconstrained
        } finally {
            loading.value = false
        }
    }

    async function reload(): Promise<void> {
        loaded.value = false
        loading.value = false
        await load()
    }

    const minValue = computed<CalendarDate | undefined>(() =>
        minDateStr.value ? strToCalendarDate(minDateStr.value) : undefined,
    )

    const maxValue = computed<CalendarDate | undefined>(() =>
        maxDateStr.value ? strToCalendarDate(maxDateStr.value) : undefined,
    )

    const sortedDates = computed<string[]>(() =>
        // ISO date strings sort lexicographically = chronologically
        [...availableDates.value].sort(),
    )

    function isDateDisabled(date: DateValue): boolean {
        if (!loaded.value) return false
        const s = `${date.year}-${String(date.month).padStart(2, '0')}-${String(date.day).padStart(2, '0')}`
        return !availableDates.value.has(s)
    }

    return { load, reload, loaded, minValue, maxValue, isDateDisabled, sortedDates }
}
