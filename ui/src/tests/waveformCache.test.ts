// @vitest-environment node
import { describe, expect, it } from 'vitest'
import type { WaveformDataResponse } from '@/types'
import {
    createWaveformCacheRegistry,
    WaveformWindowCache,
    type FetchWindow,
} from '@/utils/waveformCache'
import { BASE_MAX_POINTS, MAX_CACHED_CHUNKS, MIN_INVIEW_POINTS } from '@/constants/waveform'

function fw(startSec: number, endSec: number, maxPoints: number): FetchWindow {
    return { startSec, endSec, maxPoints }
}

function makeResponse(timestamps: number[], returnedSamples?: number): WaveformDataResponse {
    return {
        timestamps,
        values: timestamps.map((t) => t / 10),
        unit: 'L/s',
        sample_rate: 25,
        total_samples: 1_000_000,
        downsampled: true,
        returned_samples: returnedSamples ?? timestamps.length,
    }
}

/** `count` timestamps evenly spanning [start, end] inclusive. */
function linspace(start: number, end: number, count: number): number[] {
    if (count === 1) return [start]
    const step = (end - start) / (count - 1)
    return Array.from({ length: count }, (_, i) => start + i * step)
}

describe('WaveformWindowCache.resolve', () => {
    it('test_subwindow_of_exact_chunk_is_hit_with_no_fetch', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(100, 200, 2000), makeResponse(linspace(100, 200, 50), 50), 1000)

        const r = cache.resolve(120, 180, 1000)

        expect(r.hit).toBe(true)
        expect(r.fetchWindow).toBeNull()
        expect(r.slice).not.toBeNull()
        expect(r.slice!.unit).toBe('L/s')
    })

    it('test_dense_but_inexact_chunk_serves_from_cache', () => {
        const cache = new WaveformWindowCache()
        // 6000 pts over 300 s, returned == maxPoints ⇒ inexact, yet the in-view slice is dense.
        cache.store(fw(0, 300, 6000), makeResponse(linspace(0, 300, 6000), 6000), 1000)

        const r = cache.resolve(100, 200, 1000)

        expect(r.hit).toBe(true)
        expect(r.fetchWindow).toBeNull()
        expect(r.slice!.timestamps.length).toBeGreaterThanOrEqual(MIN_INVIEW_POINTS)
    })

    it('test_covered_but_sparse_returns_slice_and_fetch', () => {
        const cache = new WaveformWindowCache()
        // 100 downsampled pts over the whole chunk; a narrow view is far below the density floor.
        cache.store(fw(0, 300, 100), makeResponse(linspace(0, 300, 100), 100), 1000)

        const r = cache.resolve(100, 200, 1000)

        expect(r.hit).toBe(false)
        expect(r.slice).not.toBeNull()
        expect(r.slice!.timestamps.length).toBeLessThan(MIN_INVIEW_POINTS)
        expect(r.fetchWindow).not.toBeNull()
    })

    it('test_overlapping_not_covering_returns_partial_slice_and_fetch', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(100, 200, 2000), makeResponse(linspace(100, 200, 50), 50), 1000)

        const r = cache.resolve(150, 300, 1000)

        expect(r.hit).toBe(false)
        expect(r.slice).not.toBeNull()
        // Only data up to 200 exists; the partial slice cannot extend past the chunk.
        expect(Math.max(...r.slice!.timestamps)).toBeLessThanOrEqual(200)
        expect(r.fetchWindow).not.toBeNull()
    })

    it('test_no_overlap_returns_null_slice_and_fetch', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(100, 200, 2000), makeResponse(linspace(100, 200, 50), 50), 1000)

        const r = cache.resolve(500, 600, 1000)

        expect(r.hit).toBe(false)
        expect(r.slice).toBeNull()
        expect(r.fetchWindow).not.toBeNull()
    })

    it('test_empty_cache_miss_returns_three_times_window_with_scaled_max_points', () => {
        const cache = new WaveformWindowCache()

        const r = cache.resolve(400, 500, 1000)

        expect(r.hit).toBe(false)
        expect(r.slice).toBeNull()
        // ±1 window width around a 100 s span → [300, 600], ratio 3 → 3 * BASE_MAX_POINTS.
        expect(r.fetchWindow).toEqual({
            startSec: 300,
            endSec: 600,
            maxPoints: BASE_MAX_POINTS * 3,
        })
    })

    it('test_fetch_window_clamps_to_zero_at_start_of_night', () => {
        const cache = new WaveformWindowCache()

        const r = cache.resolve(0, 100, 1000)

        expect(r.fetchWindow!.startSec).toBe(0)
        expect(r.fetchWindow!.endSec).toBe(200)
        expect(r.fetchWindow!.maxPoints).toBe(BASE_MAX_POINTS * 2)
    })

    it('test_fetch_window_clamps_to_duration_at_end_of_night', () => {
        const cache = new WaveformWindowCache()

        const r = cache.resolve(900, 1000, 1000)

        expect(r.fetchWindow!.startSec).toBe(800)
        expect(r.fetchWindow!.endSec).toBe(1000)
    })

    it('test_fetch_window_max_points_never_exceeds_server_cap', () => {
        const cache = new WaveformWindowCache()

        const r = cache.resolve(0, 5000, 10000)

        expect(r.fetchWindow!.maxPoints).toBeLessThanOrEqual(10000)
        expect(r.fetchWindow!.maxPoints).toBeGreaterThanOrEqual(100)
    })
})

describe('WaveformWindowCache exactness', () => {
    it('test_returned_less_than_max_points_is_exact', () => {
        const cache = new WaveformWindowCache()
        // 99 < 100 ⇒ exact ⇒ even a sparse sub-window is a hit.
        cache.store(fw(0, 300, 100), makeResponse(linspace(0, 300, 99), 99), 1000)

        const r = cache.resolve(100, 200, 1000)

        expect(r.hit).toBe(true)
    })

    it('test_returned_equal_to_max_points_is_not_exact', () => {
        const cache = new WaveformWindowCache()
        // 100 == 100 boundary ⇒ not exact ⇒ a sparse sub-window misses.
        cache.store(fw(0, 300, 100), makeResponse(linspace(0, 300, 100), 100), 1000)

        const r = cache.resolve(100, 200, 1000)

        expect(r.hit).toBe(false)
    })
})

describe('WaveformWindowCache binary slice edges', () => {
    it('test_slice_includes_one_sample_past_each_edge', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(0, 50, 2000), makeResponse([0, 10, 20, 30, 40, 50], 6), 1000)

        // Interior points are 20 and 30; expect 10 (before) and 40 (after) to bracket them.
        const r = cache.resolve(15, 35, 1000)

        expect(r.slice!.timestamps).toEqual([10, 20, 30, 40])
    })

    it('test_slice_brackets_edges_when_window_lands_on_samples', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(0, 50, 2000), makeResponse([0, 10, 20, 30, 40, 50], 6), 1000)

        const r = cache.resolve(10, 40, 1000)

        // Edges 10 and 40 are on samples; the bracketing 0 and 50 are still included.
        expect(r.slice!.timestamps).toEqual([0, 10, 20, 30, 40, 50])
    })

    it('test_slice_is_empty_when_window_does_not_overlap_chunk', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(0, 50, 2000), makeResponse([0, 10, 20, 30, 40, 50], 6), 1000)

        // No covering chunk and no overlap ⇒ nothing to render.
        const r = cache.resolve(500, 600, 1000)

        expect(r.slice).toBeNull()
    })
})

describe('WaveformWindowCache LRU + overview', () => {
    // Exact, dense chunk covering [i*100, i*100+50].
    function storeWindow(cache: WaveformWindowCache, i: number): void {
        const start = i * 100
        const end = start + 50
        cache.store(fw(start, end, 2000), makeResponse(linspace(start, end, 100), 100), 100000)
    }

    it('test_oldest_chunk_evicts_past_max_cached_chunks', () => {
        const cache = new WaveformWindowCache()
        for (let i = 0; i < MAX_CACHED_CHUNKS + 1; i++) storeWindow(cache, i)

        // Chunk 0 (oldest) is evicted; chunk 1 survives.
        expect(cache.resolve(10, 40, 100000).hit).toBe(false)
        expect(cache.resolve(110, 140, 100000).hit).toBe(true)
    })

    it('test_hit_touches_lru_so_touched_chunk_survives_eviction', () => {
        const cache = new WaveformWindowCache()
        for (let i = 0; i < MAX_CACHED_CHUNKS; i++) storeWindow(cache, i) // fill the LRU exactly

        // Touch chunk 0 via a hit, then push one more chunk to force one eviction.
        expect(cache.resolve(10, 40, 100000).hit).toBe(true)
        storeWindow(cache, MAX_CACHED_CHUNKS)

        // Chunk 0 was refreshed, so chunk 1 becomes the eviction victim instead.
        expect(cache.resolve(10, 40, 100000).hit).toBe(true)
        expect(cache.resolve(110, 140, 100000).hit).toBe(false)
    })

    it('test_overview_is_never_evicted', () => {
        const cache = new WaveformWindowCache()
        // Store the full-night overview first, then flood the LRU.
        cache.store(fw(0, 10000, 2000), makeResponse(linspace(0, 10000, 2000), 2000), 10000)
        for (let i = 0; i < 9; i++) {
            const start = i * 100
            cache.store(
                fw(start, start + 50, 2000),
                makeResponse(linspace(start, start + 50, 100), 100),
                10000,
            )
        }

        // Full-night resolve is still a dense hit ⇒ the overview outlived every LRU eviction.
        const r = cache.resolve(0, 10000, 10000)
        expect(r.hit).toBe(true)
        expect(r.slice!.timestamps.length).toBeGreaterThanOrEqual(MIN_INVIEW_POINTS)
    })

    it('test_clear_empties_the_cache', () => {
        const cache = new WaveformWindowCache()
        cache.store(fw(100, 200, 2000), makeResponse(linspace(100, 200, 50), 50), 1000)
        expect(cache.resolve(120, 180, 1000).hit).toBe(true)

        cache.clear()

        const r = cache.resolve(120, 180, 1000)
        expect(r.hit).toBe(false)
        expect(r.slice).toBeNull()
    })
})

describe('createWaveformCacheRegistry', () => {
    it('test_get_cache_returns_same_instance_per_type', () => {
        const registry = createWaveformCacheRegistry()

        expect(registry.getCache('flow')).toBe(registry.getCache('flow'))
        expect(registry.getCache('flow')).not.toBe(registry.getCache('pressure'))
    })

    it('test_clear_empties_every_cache_but_keeps_instances', () => {
        const registry = createWaveformCacheRegistry()
        const flow = registry.getCache('flow')
        flow.store(fw(100, 200, 2000), makeResponse(linspace(100, 200, 50), 50), 1000)
        expect(flow.resolve(120, 180, 1000).hit).toBe(true)

        registry.clear()

        expect(registry.getCache('flow')).toBe(flow) // identity stable across clear
        expect(flow.resolve(120, 180, 1000).hit).toBe(false)
    })
})
