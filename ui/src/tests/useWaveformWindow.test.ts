import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/api/waveforms')

import { getWaveformData } from '@/api/waveforms'
import { useWaveformWindow } from '@/composables/useWaveformWindow'
import { createWaveformCacheRegistry } from '@/utils/waveformCache'
import { ZOOM_FETCH_DEBOUNCE_MS } from '@/constants/waveform'
import type { WaveformDataResponse } from '@/types'

const mockGetWaveformData = vi.mocked(getWaveformData)

function makeResponse(overrides: Partial<WaveformDataResponse> = {}): WaveformDataResponse {
    return {
        timestamps: [0, 1, 2],
        values: [1.0, 1.5, 2.0],
        unit: 'L/min',
        downsampled: false,
        returned_samples: 3,
        sample_rate: 1,
        total_samples: 3,
        ...overrides,
    }
}

// A dense full-night response that reports returned == requested, so store() marks it inexact
// (returned < maxPoints is false) — a sub-window slice is then sparse and forces a refetch.
function makeDenseOverview(points = 2000, duration = 100): WaveformDataResponse {
    const timestamps = Array.from({ length: points }, (_, i) => (i * duration) / (points - 1))
    return makeResponse({
        timestamps,
        values: timestamps.map(() => 1),
        returned_samples: points,
        total_samples: points,
    })
}

function makeComposable({ type = 'flow', duration = 100 } = {}) {
    const sessionId = ref(1)
    const waveformType = ref(type)
    const durationSec = ref(duration)
    const registry = createWaveformCacheRegistry()
    const composable = useWaveformWindow(sessionId, waveformType, registry, durationSec)
    return { composable, sessionId, waveformType, durationSec, registry }
}

// Fire the debounce timer, then let the resulting fetch's microtasks settle.
async function tick(): Promise<void> {
    vi.advanceTimersByTime(ZOOM_FETCH_DEBOUNCE_MS)
    await flushPromises()
}

beforeEach(() => {
    vi.resetAllMocks()
    vi.useFakeTimers()
})

afterEach(() => {
    vi.useRealTimers()
})

describe('useWaveformWindow', () => {
    it('test_first_load_fetches_full_night_and_stores', async () => {
        mockGetWaveformData.mockResolvedValueOnce(makeResponse({ timestamps: [0, 50, 100] }))
        const { composable } = makeComposable()

        composable.loadWindow()
        await tick()

        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)
        // Full-night fetch omits start/end bounds (matches previous behavior).
        expect(mockGetWaveformData).toHaveBeenCalledWith(
            1,
            'flow',
            { max_points: 2000 },
            expect.any(AbortSignal),
        )
        expect(composable.data.value?.timestamps).toEqual([0, 50, 100])
        expect(composable.loading.value).toBe(false)
    })

    it('test_second_load_inside_exact_region_does_not_fetch', async () => {
        mockGetWaveformData.mockResolvedValueOnce(makeResponse({ timestamps: [0, 50, 100] }))
        const { composable } = makeComposable()

        composable.loadWindow()
        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)

        // Sub-window of the exact full-night chunk: served synchronously, zero network.
        composable.loadWindow(10, 20)
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)
        expect(composable.loading.value).toBe(false)
        expect(composable.data.value).not.toBeNull()

        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)
    })

    it('test_sparse_region_serves_slice_and_fetches_denser', async () => {
        mockGetWaveformData
            .mockResolvedValueOnce(makeDenseOverview())
            .mockResolvedValueOnce(makeResponse())
        const { composable } = makeComposable()

        composable.loadWindow()
        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)

        // Covered by the inexact overview but too sparse: render the slice now, refetch denser.
        composable.loadWindow(40, 50)
        expect(composable.data.value?.timestamps.length).toBeGreaterThan(0)
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)

        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(2)
        // Expanded window (±1 width) at density-scaled max_points.
        expect(mockGetWaveformData).toHaveBeenNthCalledWith(
            2,
            1,
            'flow',
            { max_points: 6000, start_seconds: 30, end_seconds: 60 },
            expect.any(AbortSignal),
        )
    })

    it('test_rapid_successive_calls_coalesce_to_last_window', async () => {
        mockGetWaveformData.mockResolvedValue(makeResponse())
        const { composable } = makeComposable()

        // No timer advance between calls: earlier debounce timers are cancelled, last wins.
        composable.loadWindow(40, 50)
        composable.loadWindow(60, 70)
        composable.loadWindow(80, 90)
        await tick()

        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)
        expect(mockGetWaveformData).toHaveBeenCalledWith(
            1,
            'flow',
            { max_points: 6000, start_seconds: 70, end_seconds: 100 },
            expect.any(AbortSignal),
        )
    })

    it('test_new_load_aborts_the_in_flight_fetch', async () => {
        const signals: AbortSignal[] = []
        mockGetWaveformData.mockImplementation((_id, _type, _params, signal) => {
            signals.push(signal as AbortSignal)
            return new Promise<WaveformDataResponse>(() => {})
        })
        const { composable } = makeComposable()

        composable.loadWindow(40, 50)
        await tick()
        composable.loadWindow(60, 70)
        await tick()

        expect(signals).toHaveLength(2)
        expect(signals[0].aborted).toBe(true)
        expect(signals[1].aborted).toBe(false)
    })

    it('test_type_switch_and_back_reuses_registry_cache', async () => {
        mockGetWaveformData
            .mockResolvedValueOnce(makeResponse({ timestamps: [0, 50, 100] }))
            .mockResolvedValueOnce(makeResponse({ timestamps: [0, 50, 100] }))
        const { composable, waveformType } = makeComposable()

        composable.loadWindow()
        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(1)

        waveformType.value = 'pressure'
        composable.loadWindow()
        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(2)

        // Back to flow: the exact full-night chunk persists in the registry — no third fetch.
        waveformType.value = 'flow'
        composable.loadWindow()
        await tick()
        expect(mockGetWaveformData).toHaveBeenCalledTimes(2)
    })

    it('test_failed_fetch_sets_error_but_keeps_last_good_data', async () => {
        mockGetWaveformData
            .mockResolvedValueOnce(makeResponse({ timestamps: [30, 45, 60] }))
            .mockRejectedValueOnce(new Error('network failure'))
        const { composable } = makeComposable()

        composable.loadWindow(40, 50)
        await tick()
        const lastGood = composable.data.value
        expect(lastGood).not.toBeNull()

        // A disjoint window misses and its fetch fails.
        composable.loadWindow(80, 90)
        await tick()

        expect(composable.error.value).toBe('network failure')
        expect(composable.data.value).toBe(lastGood)
        expect(composable.loading.value).toBe(false)
    })

    it('test_canceled_error_is_ignored', async () => {
        const canceled = Object.assign(new Error('canceled'), { name: 'CanceledError' })
        mockGetWaveformData.mockRejectedValueOnce(canceled)
        const { composable } = makeComposable()

        composable.loadWindow()
        await tick()

        expect(composable.error.value).toBeNull()
        expect(composable.loading.value).toBe(false)
    })

    it('test_keeps_previous_data_while_reload_in_flight', async () => {
        let resolveSecond!: (v: WaveformDataResponse) => void
        mockGetWaveformData
            .mockResolvedValueOnce(makeResponse({ timestamps: [30, 45, 60] }))
            .mockReturnValueOnce(
                new Promise<WaveformDataResponse>((res) => {
                    resolveSecond = res
                }),
            )
        const { composable } = makeComposable()

        composable.loadWindow(40, 50)
        await tick()
        const firstData = composable.data.value
        expect(firstData).not.toBeNull()

        // Disjoint window: no cached slice to show, so data stays while the fetch is in flight.
        composable.loadWindow(80, 90)
        await tick()

        expect(composable.loading.value).toBe(true)
        expect(composable.data.value).toBe(firstData)

        resolveSecond(makeResponse())
        await flushPromises()
    })

    it('test_superseded_call_does_not_clear_loading_while_newer_pending', async () => {
        let rejectFirst!: (e: unknown) => void
        let resolveSecond!: (v: WaveformDataResponse) => void
        mockGetWaveformData
            .mockReturnValueOnce(
                new Promise<WaveformDataResponse>((_res, rej) => {
                    rejectFirst = rej
                }),
            )
            .mockReturnValueOnce(
                new Promise<WaveformDataResponse>((res) => {
                    resolveSecond = res
                }),
            )
        const { composable } = makeComposable()

        composable.loadWindow(40, 50)
        await tick()
        composable.loadWindow(80, 90)
        await tick()

        // First fetch was aborted by the second; its CanceledError must not clear loading.
        rejectFirst(Object.assign(new Error('canceled'), { name: 'CanceledError' }))
        await flushPromises()
        expect(composable.loading.value).toBe(true)

        resolveSecond(makeResponse())
        await flushPromises()
        expect(composable.loading.value).toBe(false)
    })

    it('test_reset_nulls_data_error_and_loading', async () => {
        mockGetWaveformData.mockResolvedValueOnce(makeResponse())
        const { composable } = makeComposable()

        composable.loadWindow()
        await tick()
        expect(composable.data.value).not.toBeNull()

        composable.reset()

        expect(composable.data.value).toBeNull()
        expect(composable.error.value).toBeNull()
        expect(composable.loading.value).toBe(false)
    })
})
