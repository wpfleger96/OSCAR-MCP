/** Shared date/time formatting helpers. */

/** e.g. "Jan 5, 03:12 AM" — short date with time, no year. */
export function formatDateShort(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

/** e.g. "Jan 5, 2024" — full date, no time. */
export function formatDateFull(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

/** e.g. "Fri, Jan 5, 2024" — full date with weekday. */
export function formatDateWithWeekday(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        weekday: 'short',
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

/** e.g. "Jan 5, 2024, 03:12 AM" — full date with time. */
export function formatDateTime(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

/** e.g. "Jan 5" — month and day only. */
export function formatDateMonthDay(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** e.g. "2024-01-05" — ISO calendar date in local time. */
export function formatIso(d: Date): string {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
}

/** e.g. "1:02:03" — clock-style h:mm:ss offset from a duration in seconds. */
export function formatTimeOffset(secs: number): string {
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    const s = Math.floor(secs % 60)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
