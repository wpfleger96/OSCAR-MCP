import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/api/waveforms')

import { getWaveformData } from '@/api/waveforms'
import { WaveformWindowCache } from '@/utils/waveformCache'
import { prefetchAdjacentWindows } from '@/utils/waveformPrefetch'
import type { WaveformDataResponse } from '@/types'

const mockGetWaveformData = vi.mocked(getWaveformData)

const DURATION = 100

function makeResponse(overrides: Partial<WaveformDataResponse> = {}): WaveformDataResponse {
    return {
        timestamps: [0, 1, 2],
        values: [1, 1, 1],
        unit: 'L/min',
        downsampled: false,
        returned_samples: 3,
        sample_rate: 1,
        total_samples: 3,
        ...overrides,
    }
}

// Evenly spaced points across [start, end]; returned == points so store() can decide exactness
// against the requested max_points.
function makeWindow(start: number, end: number, points: number): WaveformDataResponse {
    const timestamps = Array.from(
        { length: points },
        (_, i) => start + (i * (end - start)) / (points - 1),
    )
    return makeResponse({
        timestamps,
        values: timestamps.map(() => 1),
        returned_samples: points,
        total_samples: points,
    })
}

// Store a full-night overview directly (bypassing the composable) to seed a cache under test.
function seedOverview(cache: WaveformWindowCache, points: number): void {
    cache.store(
        { startSec: 0, endSec: DURATION, maxPoints: 2000 },
        makeWindow(0, DURATION, points),
        DURATION,
    )
}

function optsFor(cache: WaveformWindowCache, startSec: number, endSec: number) {
    return {
        cache,
        sessionId: 1,
        waveformType: 'flow',
        startSec,
        endSec,
        durationSec: DURATION,
        signal: new AbortController().signal,
    }
}

beforeEach(() => {
    vi.resetAllMocks()
})

describe('prefetchAdjacentWindows', () => {
    it('test_covered_dense_neighbors_trigger_no_fetch', async () => {
        const cache = new WaveformWindowCache()
        // Exact overview (returned 3 < 2000): every sub-window resolves as a hit.
        cache.store({ startSec: 0, endSec: DURATION, maxPoints: 2000 }, makeResponse(), DURATION)

        prefetchAdjacentWindows(optsFor(cache, 40, 60))
        await flushPromises()

        expect(mockGetWaveformData).not.toHaveBeenCalled()
    })

    it('test_missing_zoom_in_target_is_fetched_and_stored', async () => {
        const cache = new WaveformWindowCache()
        // Dense but inexact overview: wide windows hit, but a small zoom-in slice is too sparse.
        seedOverview(cache, 2000)
        // The zoom-in fetch window ([20, 80]) comes back dense + exact so it wins on re-resolve.
        mockGetWaveformData.mockResolvedValue(makeWindow(20, 80, 3000))

        // Window [30, 70]: zoom-in target [40, 60] is a miss; zoom-out [10, 90] stays a dense hit.
        prefetchAdjacentWindows(optsFor(cache, 30, 70))
        await flushPromises()

        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)
        expect(mockGetWaveformData).toHaveBeenCalledWith(
            1,
            'flow',
            { max_points: 6000, start_seconds: 20, end_seconds: 80 },
            expect.any(AbortSignal),
        )
        // The zoom-in window now resolves without a network round trip.
        expect(cache.resolve(40, 60, DURATION).hit).toBe(true)
    })

    it('test_zoom_out_at_full_night_does_not_fetch', async () => {
        const cache = new WaveformWindowCache()
        seedOverview(cache, 2000)

        // Already at the full night: the zoom-out candidate equals the current view and is skipped;
        // the zoom-in candidate is covered densely by the overview.
        prefetchAdjacentWindows(optsFor(cache, 0, DURATION))
        await flushPromises()

        expect(mockGetWaveformData).not.toHaveBeenCalled()
    })

    it('test_fetch_error_is_silent_and_stores_nothing', async () => {
        const cache = new WaveformWindowCache()
        seedOverview(cache, 2000)
        mockGetWaveformData.mockRejectedValue(new Error('network failure'))

        // No throw escapes fire-and-forget prefetch.
        expect(() => prefetchAdjacentWindows(optsFor(cache, 30, 70))).not.toThrow()
        await flushPromises()

        expect(mockGetWaveformData).toHaveBeenCalled()
        // The failed zoom-in target remains a miss — nothing was stored.
        expect(cache.resolve(40, 60, DURATION).hit).toBe(false)
    })

    it('test_aborted_fetch_is_silent_and_stores_nothing', async () => {
        const cache = new WaveformWindowCache()
        seedOverview(cache, 2000)
        const canceled = Object.assign(new Error('canceled'), { name: 'CanceledError' })
        mockGetWaveformData.mockRejectedValue(canceled)

        expect(() => prefetchAdjacentWindows(optsFor(cache, 30, 70))).not.toThrow()
        await flushPromises()

        expect(cache.resolve(40, 60, DURATION).hit).toBe(false)
    })

    it('test_both_candidates_can_fetch_concurrently', async () => {
        const cache = new WaveformWindowCache()
        // No overview: both zoom-in and zoom-out miss and must each fetch.
        mockGetWaveformData.mockImplementation((_id, _type, params) => {
            const p = (params ?? {}) as { start_seconds?: number; end_seconds?: number }
            return Promise.resolve(
                makeWindow(p.start_seconds ?? 0, p.end_seconds ?? DURATION, 3000),
            )
        })

        // Window [40, 60] centered mid-night: zoom-in [45, 55] and zoom-out [30, 70] both miss.
        prefetchAdjacentWindows(optsFor(cache, 40, 60))
        await flushPromises()

        expect(mockGetWaveformData).toHaveBeenCalledTimes(2)
    })
})
