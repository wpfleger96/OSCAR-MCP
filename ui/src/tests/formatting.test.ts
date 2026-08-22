// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { formatWallClockTime, nullReasonLabel } from '@/utils/formatting'

describe('nullReasonLabel', () => {
    it('test_known_code_returns_sentence', () => {
        expect(nullReasonLabel('analysis_not_run')).toBe(
            'Breath analysis has not been run for this night.',
        )
    })

    it('test_unknown_code_prettifies_underscores', () => {
        expect(nullReasonLabel('some_future_reason')).toBe('some future reason')
    })

    // The six codes below were added alongside the FL/RERA day-view metrics; a
    // mapped code must resolve to its full sentence, never the prettify fallback.
    it.each([
        'primary_mode_mismatch',
        'smart_ramp_indeterminate',
        'segments_unknown',
        'multi_session_ambiguity',
        'unvalidated_device',
        'rx_changed_within_epoch',
    ])('test_mapped_code_%s_resolves_to_full_sentence', (code) => {
        const label = nullReasonLabel(code)
        expect(label).not.toBe(code.replace(/_/g, ' '))
        expect(label?.endsWith('.')).toBe(true)
    })

    it('test_nullish_reason_returns_null', () => {
        expect(nullReasonLabel(null)).toBeNull()
        expect(nullReasonLabel(undefined)).toBeNull()
        expect(nullReasonLabel('')).toBeNull()
    })
})

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
