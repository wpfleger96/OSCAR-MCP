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
import {
    BASE_MAX_POINTS,
    MAX_CACHED_CHUNKS,
    MIN_INVIEW_POINTS,
    SERVER_MAX_POINTS,
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

/**
 * Slice a chunk to [startSec, endSec], including one sample past each edge so the
 * rendered line reaches the viewport borders. Returns an empty slice when the
 * window does not overlap the chunk's data.
 */
function sliceChunk(chunk: Chunk, startSec: number, endSec: number): WaveformSlice {
    const ts = chunk.timestamps
    const n = ts.length
    if (n === 0 || endSec < ts[0] - EPS || startSec > ts[n - 1] + EPS) {
        return { timestamps: [], values: [], unit: chunk.unit }
    }
    // lowerBound - 1 = one sample before the left edge; upperBound = one sample after the right edge.
    const from = Math.max(0, lowerBound(ts, startSec) - 1)
    const to = Math.min(n - 1, upperBound(ts, endSec))
    return {
        timestamps: ts.slice(from, to + 1),
        values: chunk.values.slice(from, to + 1),
        unit: chunk.unit,
    }
}

export class WaveformWindowCache {
    private chunks: Chunk[] = [] // LRU order: front = least recently used
    private overview: Chunk | null = null // full-night chunk, never evicted

    resolve(startSec: number, endSec: number, durationSec: number): ResolveResult {
        const candidates = this.candidates()

        // Prefer a chunk that fully covers the requested window, densest first.
        let best: { chunk: Chunk; slice: WaveformSlice } | null = null
        for (const chunk of candidates) {
            if (chunk.startSec <= startSec + EPS && chunk.endSec >= endSec - EPS) {
                const slice = sliceChunk(chunk, startSec, endSec)
                if (best === null || slice.timestamps.length > best.slice.timestamps.length) {
                    best = { chunk, slice }
                }
            }
        }
        if (best !== null) {
            const dense = best.slice.timestamps.length >= MIN_INVIEW_POINTS
            if (best.chunk.exact || dense) {
                this.touch(best.chunk)
                return { slice: best.slice, hit: true, fetchWindow: null }
            }
            return {
                slice: best.slice,
                hit: false,
                fetchWindow: this.expand(startSec, endSec, durationSec),
            }
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
        if (fetchWindow.startSec <= EPS && fetchWindow.endSec >= durationSec - EPS) {
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

    /** Grow the window by ±1 width (≈3× span) and scale max_points to hold in-view density. */
    private expand(startSec: number, endSec: number, durationSec: number): FetchWindow {
        const w = endSec - startSec
        if (w <= 0) {
            return {
                startSec: Math.max(0, startSec),
                endSec: Math.min(durationSec, endSec),
                maxPoints: BASE_MAX_POINTS,
            }
        }
        const start = Math.max(0, startSec - w)
        const end = Math.min(durationSec, endSec + w)
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
