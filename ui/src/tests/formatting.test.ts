// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { formatWallClockTime } from '@/utils/formatting'

describe('formatWallClockTime', () => {
    // Build epoch via local-time constructor to avoid UTC/timezone dependency across machines.
    // The date component is irrelevant — only the time fields (h/m/s) are asserted.
    const epoch = new Date(2026, 3, 6, 22, 30, 15).getTime() / 1000 // 22:30:15 local

    it('test_below_60_incr_includes_seconds_in_output', () => {
        const result = formatWallClockTime(epoch, 30)
        expect(result).toContain('15')
    })

    it('test_below_60_incr_has_two_colon_separators', () => {
        const result = formatWallClockTime(epoch, 30)
        // h:mm:ss produces two colons regardless of locale 12/24h choice
        const colonCount = (result.match(/:/g) ?? []).length
        expect(colonCount).toBe(2)
    })

    it('test_exactly_60_incr_excludes_seconds', () => {
        // foundIncr === 60 is the boundary: seconds must NOT appear
        const result = formatWallClockTime(epoch, 60)
        expect(result).not.toContain('15')
    })

    it('test_exactly_60_incr_has_one_colon_separator', () => {
        const result = formatWallClockTime(epoch, 60)
        // h:mm produces one colon regardless of locale 12/24h choice
        const colonCount = (result.match(/:/g) ?? []).length
        expect(colonCount).toBe(1)
    })

    it('test_above_60_incr_excludes_seconds', () => {
        const result = formatWallClockTime(epoch, 300)
        expect(result).not.toContain('15')
        const colonCount = (result.match(/:/g) ?? []).length
        expect(colonCount).toBe(1)
    })

    it('test_minute_value_appears_in_output', () => {
        const result = formatWallClockTime(epoch, 60)
        expect(result).toContain('30')
    })
})
