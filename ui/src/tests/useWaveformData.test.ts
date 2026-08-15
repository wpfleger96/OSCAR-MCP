import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/api/waveforms')

import { getWaveformData } from '@/api/waveforms'
import { useWaveformData } from '@/composables/useWaveformData'
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

function makeComposable(type = 'flow') {
    const sessionId = ref(1)
    const waveformType = ref(type)
    return { composable: useWaveformData(sessionId, waveformType), sessionId, waveformType }
}

beforeEach(() => {
    vi.resetAllMocks()
})

describe('useWaveformData', () => {
    it('test_loadData_keeps_previous_data_while_reload_in_flight', async () => {
        // Arrange: first load succeeds, second load is deferred
        const firstResponse = makeResponse()
        let resolveSecond!: (v: WaveformDataResponse) => void
        const secondPromise = new Promise<WaveformDataResponse>((res) => {
            resolveSecond = res
        })

        mockGetWaveformData.mockResolvedValueOnce(firstResponse).mockReturnValueOnce(secondPromise)

        const { composable } = makeComposable()

        // Act: first load
        await composable.loadData()
        expect(composable.data.value).toEqual(firstResponse)

        // Act: start second load — do NOT await it yet
        void composable.loadData()
        await Promise.resolve() // allow the call to set loading

        // Assert: data from first load is still visible while second is in flight
        expect(composable.loading.value).toBe(true)
        expect(composable.data.value).toEqual(firstResponse)

        // Cleanup: resolve second so test doesn't hang
        resolveSecond(makeResponse())
        await flushPromises()
    })

    it('test_superseded_call_does_not_clear_loading_while_newer_call_pending', async () => {
        // Arrange: first call gets canceled, second call is deferred
        let resolveSecond!: (v: WaveformDataResponse) => void
        const secondDeferred = new Promise<WaveformDataResponse>((res) => {
            resolveSecond = res
        })

        // First call: rejects immediately with CanceledError (simulates being aborted)
        const canceledError = Object.assign(new Error('canceled'), { name: 'CanceledError' })
        mockGetWaveformData.mockRejectedValueOnce(canceledError).mockReturnValueOnce(secondDeferred)

        const { composable } = makeComposable()

        // Act: start first call, then immediately start second before first finishes
        void composable.loadData()
        void composable.loadData()

        // Let microtasks run so the first call's rejection is processed
        await Promise.resolve()
        await Promise.resolve()

        // Assert: loading is still true — the superseded call must not have cleared it
        expect(composable.loading.value).toBe(true)

        // Cleanup: resolve the second call
        resolveSecond(makeResponse())
        await flushPromises()
        expect(composable.loading.value).toBe(false)
    })

    it('test_failed_reload_sets_error_but_keeps_stale_data', async () => {
        // Arrange: first load succeeds, second load fails
        const firstResponse = makeResponse()
        mockGetWaveformData
            .mockResolvedValueOnce(firstResponse)
            .mockRejectedValueOnce(new Error('network failure'))

        const { composable } = makeComposable()

        await composable.loadData()
        expect(composable.data.value).toEqual(firstResponse)

        // Act: reload fails
        await composable.loadData()

        // Assert: stale data remains visible, error is set
        expect(composable.data.value).toEqual(firstResponse)
        expect(composable.error.value).toBe('network failure')
    })

    it('test_reset_nulls_data_error_and_loading', async () => {
        // Arrange: load some data
        mockGetWaveformData.mockResolvedValueOnce(makeResponse())
        const { composable } = makeComposable()
        await composable.loadData()
        expect(composable.data.value).not.toBeNull()

        // Act
        composable.reset()

        // Assert
        expect(composable.data.value).toBeNull()
        expect(composable.error.value).toBeNull()
        expect(composable.loading.value).toBe(false)
    })
})
