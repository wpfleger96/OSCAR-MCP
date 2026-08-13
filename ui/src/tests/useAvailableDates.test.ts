// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { adjacentDates } from '@/composables/useAvailableDates'

describe('adjacentDates', () => {
    it('test_empty_array_returns_both_null', () => {
        const result = adjacentDates([], '2026-01-05')
        expect(result).toEqual({ prev: null, next: null })
    })

    it('test_single_element_is_current_returns_both_null', () => {
        const result = adjacentDates(['2026-01-05'], '2026-01-05')
        expect(result).toEqual({ prev: null, next: null })
    })

    it('test_current_at_first_position_returns_null_prev', () => {
        const sorted = ['2026-01-01', '2026-01-05', '2026-01-09']
        const result = adjacentDates(sorted, '2026-01-01')
        expect(result).toEqual({ prev: null, next: '2026-01-05' })
    })

    it('test_current_at_last_position_returns_null_next', () => {
        const sorted = ['2026-01-01', '2026-01-05', '2026-01-09']
        const result = adjacentDates(sorted, '2026-01-09')
        expect(result).toEqual({ prev: '2026-01-05', next: null })
    })

    it('test_current_in_middle_returns_adjacent_entries_not_calendar_neighbors', () => {
        // Neighbors are adjacent entries in the sorted array, not calendar-adjacent days.
        // ['2026-01-01', '2026-01-05', '2026-01-09']: current '2026-01-05' → prev '2026-01-01', next '2026-01-09'
        const sorted = ['2026-01-01', '2026-01-05', '2026-01-09']
        const result = adjacentDates(sorted, '2026-01-05')
        expect(result).toEqual({ prev: '2026-01-01', next: '2026-01-09' })
    })

    it('test_current_not_present_returns_both_null', () => {
        const sorted = ['2026-01-01', '2026-01-05', '2026-01-09']
        const result = adjacentDates(sorted, '2026-01-03')
        expect(result).toEqual({ prev: null, next: null })
    })
})
