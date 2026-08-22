/** Shared date/time, quantity, and classification formatting helpers. */

export function ahiClass(ahi: number | null | undefined): string {
    if (ahi == null) return ''
    if (ahi < 5) return 'ahi-good'
    if (ahi < 15) return 'ahi-mild'
    return 'ahi-severe'
}

/** e.g. parseLocalDate("2026-05-28") — treats date-only strings as local midnight to avoid UTC shift. */
export function parseLocalDate(iso: string): Date {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    return new Date(iso)
}

/** e.g. "Jan 5, 03:12 AM" — short date with time, no year. */
export function formatDateShort(iso: string): string {
    return parseLocalDate(iso).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

/** e.g. "Jan 5, 2024" — full date, no time. */
export function formatDateFull(iso: string): string {
    return parseLocalDate(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

/** e.g. "Fri, Jan 5, 2024" — full date with weekday. */
export function formatDateWithWeekday(iso: string): string {
    return parseLocalDate(iso).toLocaleDateString(undefined, {
        weekday: 'short',
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

/** e.g. "Jan 5, 2024, 03:12 AM" — full date with time. */
export function formatDateTime(iso: string): string {
    return parseLocalDate(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

/** e.g. "Jan 5" — month and day only. */
export function formatDateMonthDay(iso: string): string {
    return parseLocalDate(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** e.g. "just now", "5m ago", "3h ago", or "Jan 5, 03:12 AM" for older times.
 *  Accepts an ISO 8601 string (import endpoint) or epoch seconds (analysis endpoint). */
export function formatRelativeTime(value: string | number): string {
    const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
    if (isNaN(d.getTime())) return '—'
    const diffMin = Math.floor((Date.now() - d.getTime()) / 60_000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffH = Math.floor(diffMin / 60)
    if (diffH < 24) return `${diffH}h ago`
    return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
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

/** e.g. "325.5 KB", "1.2 GB" — human-readable byte count. */
export function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

// Lazily-created, module-cached formatters for uPlot axis tick labels (allocation-free on hot path).
let _fmtWithSecs: Intl.DateTimeFormat | undefined
let _fmtNoSecs: Intl.DateTimeFormat | undefined

/** e.g. "10:30 PM" or "10:30:15 PM" — wall-clock time for uPlot axis ticks.
 *  Shows seconds when the tick increment is finer than 1 minute (`foundIncr < 60`). */
export function formatWallClockTime(epochSecs: number, foundIncr: number): string {
    const d = new Date(epochSecs * 1000)
    if (foundIncr < 60) {
        _fmtWithSecs ??= new Intl.DateTimeFormat(undefined, {
            hour: 'numeric',
            minute: '2-digit',
            second: '2-digit',
        })
        return _fmtWithSecs.format(d)
    }
    _fmtNoSecs ??= new Intl.DateTimeFormat(undefined, {
        hour: 'numeric',
        minute: '2-digit',
    })
    return _fmtNoSecs.format(d)
}

/** Convert seconds to fractional hours; returns null when the input is null or undefined. */
export function secToHours(sec: number | null | undefined): number | null {
    return sec != null ? sec / 3600 : null
}

/** Average of a nullable array; returns null when there are no non-null values. */
export function avg(vals: (number | null | undefined)[]): number | null {
    const nonNull = vals.filter((v): v is number => v != null)
    return nonNull.length ? nonNull.reduce((a, b) => a + b, 0) / nonNull.length : null
}
