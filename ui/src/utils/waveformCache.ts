/**
 * Pure client-side cache of waveform windows for the session-detail charts.
 *
 * A chunk is one API response remembered with the request window that produced it.
 * Any sub-window of an exact chunk is servable forever; an inexact chunk is servable
 * only when the sliced sub-window still carries enough points to render densely.
 * Chunks are never merged or stitched. The full-night overview chunk is never evicted
 * so Reset Zoom is always instant; all other chunks evict LRU past MAX_CACHED_CHUNKS.
 */
import type { WaveformDataResponse } from '@/types'
import type { WaveformDataParams } from '@/api/waveforms'
import {
    BASE_MAX_POINTS,
    MAX_CACHED_CHUNKS,
    MIN_INVIEW_POINTS,
    SERVER_MAX_POINTS,
    WINDOW_EXPANSION,
} from '@/constants/waveform'

// Tolerance for comparing second-resolution window bounds (fractional sample rates).
const EPS = 1e-6

export interface WaveformSlice {
    timestamps: number[] // session-relative seconds, ascending
    values: number[]
    unit: string
}

export interface FetchWindow {
    startSec: number
    endSec: number
    maxPoints: number
}

export interface ResolveResult {
    slice: WaveformSlice | null // best available NOW — render immediately (null if nothing overlaps)
    hit: boolean // true ⇒ no network needed
    fetchWindow: FetchWindow | null // set iff !hit
}

/** EPS-tolerant: does [startSec, endSec] span the whole session? */
export function spansFullNight(startSec: number, endSec: number, durationSec: number): boolean {
    return startSec <= EPS && endSec >= durationSec - EPS
}

/**
 * Query params for a fetch window. Full-night windows omit start/end bounds so the request never
 * clips when the reported duration rounds below the last sample (fractional sample rates).
 */
export function buildWaveformParams(
    fetchWindow: FetchWindow,
    durationSec: number,
): WaveformDataParams {
    const params: WaveformDataParams = { max_points: fetchWindow.maxPoints }
    if (!spansFullNight(fetchWindow.startSec, fetchWindow.endSec, durationSec)) {
        params.start_seconds = fetchWindow.startSec
        params.end_seconds = fetchWindow.endSec
    }
    return params
}

interface Chunk {
    startSec: number
    endSec: number
    timestamps: number[]
    values: number[]
    unit: string
    exact: boolean
    maxPoints: number
}

function clamp(value: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, value))
}

/** First index i with arr[i] >= target (arr ascending). */
function lowerBound(arr: number[], target: number): number {
    let lo = 0
    let hi = arr.length
    while (lo < hi) {
        const mid = (lo + hi) >>> 1
        if (arr[mid] < target) lo = mid + 1
        else hi = mid
    }
    return lo
}

/** First index i with arr[i] > target (arr ascending). */
function upperBound(arr: number[], target: number): number {
    let lo = 0
    let hi = arr.length
    while (lo < hi) {
        const mid = (lo + hi) >>> 1
        if (arr[mid] <= target) lo = mid + 1
        else hi = mid
    }
    return lo
}

interface SliceBounds {
    from: number
    to: number
    count: number
}

/**
 * Index bounds of the slice of [startSec, endSec] out of a chunk, including one sample past each
 * edge so the rendered line reaches the viewport borders. No allocation — callers materialize only
 * the winner. Returns null when the window does not overlap the chunk's data.
 */
function sliceBounds(chunk: Chunk, startSec: number, endSec: number): SliceBounds | null {
    const ts = chunk.timestamps
    const n = ts.length
    if (n === 0 || endSec < ts[0] - EPS || startSec > ts[n - 1] + EPS) return null
    // lowerBound - 1 = one sample before the left edge; upperBound = one sample after the right edge.
    const from = Math.max(0, lowerBound(ts, startSec) - 1)
    const to = Math.min(n - 1, upperBound(ts, endSec))
    return { from, to, count: to - from + 1 }
}

/** Copy the [from, to] sample range out of a chunk into a renderable slice. */
function materialize(chunk: Chunk, from: number, to: number): WaveformSlice {
    return {
        timestamps: chunk.timestamps.slice(from, to + 1),
        values: chunk.values.slice(from, to + 1),
        unit: chunk.unit,
    }
}

/** Slice a chunk to [startSec, endSec]; empty when the window does not overlap the chunk's data. */
function sliceChunk(chunk: Chunk, startSec: number, endSec: number): WaveformSlice {
    const b = sliceBounds(chunk, startSec, endSec)
    return b === null
        ? { timestamps: [], values: [], unit: chunk.unit }
        : materialize(chunk, b.from, b.to)
}

export class WaveformWindowCache {
    private chunks: Chunk[] = [] // LRU order: front = least recently used
    private overview: Chunk | null = null // full-night chunk, never evicted

    resolve(startSec: number, endSec: number, durationSec: number): ResolveResult {
        const candidates = this.candidates()

        // Among chunks that fully cover the window: an exact cover is native resolution and cannot
        // be improved, so it wins outright even over a denser-slicing inexact cover (refetching it
        // would be pure waste). Otherwise the densest cover serves if it clears the density floor.
        // Slice bounds are computed per candidate without allocation; only the winner materializes.
        let bestExact: (SliceBounds & { chunk: Chunk }) | null = null
        let densest: (SliceBounds & { chunk: Chunk }) | null = null
        for (const chunk of candidates) {
            if (chunk.startSec > startSec + EPS || chunk.endSec < endSec - EPS) continue
            const b = sliceBounds(chunk, startSec, endSec)
            const ref = b ? { chunk, ...b } : { chunk, from: 0, to: -1, count: 0 }
            if (densest === null || ref.count > densest.count) densest = ref
            if (chunk.exact && (bestExact === null || ref.count > bestExact.count)) bestExact = ref
        }
        if (bestExact !== null) {
            this.touch(bestExact.chunk)
            return {
                slice: materialize(bestExact.chunk, bestExact.from, bestExact.to),
                hit: true,
                fetchWindow: null,
            }
        }
        if (densest !== null) {
            const slice = materialize(densest.chunk, densest.from, densest.to)
            if (densest.count >= MIN_INVIEW_POINTS) {
                this.touch(densest.chunk)
                return { slice, hit: true, fetchWindow: null }
            }
            return { slice, hit: false, fetchWindow: this.expand(startSec, endSec, durationSec) }
        }

        // No full cover: serve the largest-overlap partial slice while we fetch.
        let partial: WaveformSlice | null = null
        let bestOverlap = 0
        for (const chunk of candidates) {
            const overlap = Math.min(endSec, chunk.endSec) - Math.max(startSec, chunk.startSec)
            if (overlap > bestOverlap) {
                const slice = sliceChunk(chunk, startSec, endSec)
                if (slice.timestamps.length > 0) {
                    bestOverlap = overlap
                    partial = slice
                }
            }
        }
        return {
            slice: partial,
            hit: false,
            fetchWindow: this.expand(startSec, endSec, durationSec),
        }
    }

    store(fetchWindow: FetchWindow, response: WaveformDataResponse, durationSec: number): void {
        // returned < requested ⇒ no LTTB ran ⇒ native resolution for this window.
        const exact = response.returned_samples < fetchWindow.maxPoints
        const chunk: Chunk = {
            startSec: fetchWindow.startSec,
            endSec: fetchWindow.endSec,
            timestamps: response.timestamps,
            values: response.values,
            unit: response.unit,
            exact,
            maxPoints: fetchWindow.maxPoints,
        }
        if (spansFullNight(fetchWindow.startSec, fetchWindow.endSec, durationSec)) {
            this.overview = chunk
            return
        }
        this.chunks.push(chunk)
        while (this.chunks.length > MAX_CACHED_CHUNKS) {
            this.chunks.shift()
        }
    }

    clear(): void {
        this.chunks = []
        this.overview = null
    }

    private candidates(): Chunk[] {
        return this.overview ? [...this.chunks, this.overview] : [...this.chunks]
    }

    /** Move a hit chunk to the most-recently-used end. The overview is not in the LRU list. */
    private touch(chunk: Chunk): void {
        const i = this.chunks.indexOf(chunk)
        if (i >= 0 && i !== this.chunks.length - 1) {
            this.chunks.splice(i, 1)
            this.chunks.push(chunk)
        }
    }

    /** Grow the window to WINDOW_EXPANSION× span (±(WINDOW_EXPANSION-1)/2 widths) and scale
     * max_points off the actual clamped bounds to hold in-view density. */
    private expand(startSec: number, endSec: number, durationSec: number): FetchWindow {
        const w = endSec - startSec
        if (w <= 0) {
            return {
                startSec: Math.max(0, startSec),
                endSec: Math.min(durationSec, endSec),
                maxPoints: BASE_MAX_POINTS,
            }
        }
        const grow = w * ((WINDOW_EXPANSION - 1) / 2)
        const start = Math.max(0, startSec - grow)
        const end = Math.min(durationSec, endSec + grow)
        const ratio = (end - start) / w
        const maxPoints = clamp(Math.round(BASE_MAX_POINTS * ratio), 100, SERVER_MAX_POINTS)
        return { startSec: start, endSec: end, maxPoints }
    }
}

export interface WaveformCacheRegistry {
    getCache(type: string): WaveformWindowCache // lazily instantiates, stable per type
    clear(): void // empties every cache
}

export function createWaveformCacheRegistry(): WaveformCacheRegistry {
    const caches = new Map<string, WaveformWindowCache>()
    return {
        getCache(type: string): WaveformWindowCache {
            let cache = caches.get(type)
            if (!cache) {
                cache = new WaveformWindowCache()
                caches.set(type, cache)
            }
            return cache
        },
        clear(): void {
            // Keep instances so getCache stays identity-stable across a session reset.
            for (const cache of caches.values()) cache.clear()
        },
    }
}
