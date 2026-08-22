// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { clampZoomWindow, zoomInWindow, zoomOutWindow } from '@/utils/zoomMath'
import { MIN_ZOOM_WINDOW_SEC } from '@/constants/waveform'

describe('clampZoomWindow', () => {
    it('test_centered_window_is_not_shifted', () => {
        expect(clampZoomWindow(50, 10, 100)).toEqual({ start: 40, end: 60 })
    })

    it('test_edge_shifts_at_start_to_preserve_width', () => {
        // center - halfWidth < 0: the window pins to 0 and shifts its far edge out to keep width.
        expect(clampZoomWindow(2, 5, 100)).toEqual({ start: 0, end: 10 })
    })

    it('test_edge_shifts_at_end_to_preserve_width', () => {
        // center + halfWidth > duration: the window pins to duration and shifts its near edge in.
        expect(clampZoomWindow(98, 5, 100)).toEqual({ start: 90, end: 100 })
    })
})

describe('zoomInWindow', () => {
    it('test_floor_bites_eight_second_window_yields_exactly_five', () => {
        // 8 s / 4 = 2 s half-width is below the 2.5 s floor, so the result floors to 5 s wide.
        const w = zoomInWindow(46, 54, 100)
        expect(w.end - w.start).toBeCloseTo(MIN_ZOOM_WINDOW_SEC, 10)
        expect(w).toEqual({ start: 47.5, end: 52.5 })
    })

    it('test_twenty_second_window_yields_ten', () => {
        // 20 s / 4 = 5 s half-width clears the floor, so the result is a 10 s window.
        const w = zoomInWindow(40, 60, 100)
        expect(w.end - w.start).toBeCloseTo(10, 10)
        expect(w).toEqual({ start: 45, end: 55 })
    })

    it('test_short_session_stays_full_span', () => {
        // Duration 3 s < 5 s floor: zooming in cannot go below the full span.
        expect(zoomInWindow(0, 3, 3)).toEqual({ start: 0, end: 3 })
    })
})

describe('zoomOutWindow', () => {
    it('test_doubles_the_window', () => {
        expect(zoomOutWindow(40, 60, 100)).toEqual({ start: 30, end: 70 })
    })

    it('test_clamps_and_edge_shifts_at_start', () => {
        // Doubling [10, 30] to width 40 runs past 0; the window pins to 0 and keeps its width.
        expect(zoomOutWindow(10, 30, 100)).toEqual({ start: 0, end: 40 })
    })

    it('test_clamps_and_edge_shifts_at_end', () => {
        // Doubling [80, 100] to width 40 runs past duration; it pins to 100 and keeps its width.
        expect(zoomOutWindow(80, 100, 100)).toEqual({ start: 60, end: 100 })
    })

    it('test_short_session_stays_full_span', () => {
        expect(zoomOutWindow(0, 3, 3)).toEqual({ start: 0, end: 3 })
    })
})
