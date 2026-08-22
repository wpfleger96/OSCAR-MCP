/**
 * Background warming of the cache for the next zoom step.
 *
 * Stepwise button zooms miss predictably (roughly every second step, when an expanded chunk's
 * density runs out). After a zoom settles we resolve the windows the user is most likely to ask
 * for next — one zoom-in step and one zoom-out step, using the same math as SessionDetailView —
 * and fetch the misses in the background so the next step resolves as a hit whose first render is
 * already final. Fire-and-forget: every error (abort included) is swallowed and no component
 * state is ever touched.
 */
import { getWaveformData, type WaveformDataParams } from '@/api/waveforms'
import { MIN_ZOOM_WINDOW_SEC } from '@/constants/waveform'
import type { WaveformWindowCache } from './waveformCache'

// Tolerance for comparing second-resolution window bounds (fractional sample rates).
const EPS = 1e-6

export interface PrefetchOptions {
    cache: WaveformWindowCache
    sessionId: number
    waveformType: string
    startSec: number // the window the user is NOW looking at (session-relative)
    endSec: number
    durationSec: number
    signal: AbortSignal
}

interface Window {
    start: number
    end: number
}

/** Mirror of SessionDetailView.clampZoomRange: clamp into [0, duration], edge-shifting on a hit. */
function clampWindow(center: number, halfWidth: number, duration: number): Window {
    let start = Math.max(0, center - halfWidth)
    let end = Math.min(duration, center + halfWidth)
    const desired = halfWidth * 2
    if (start === 0) end = Math.min(duration, desired)
    else if (end === duration) start = Math.max(0, duration - desired)
    return { start, end }
}

/** Fire-and-forget: warm the cache for the next zoom-in/out steps. Never touches component state. */
export function prefetchAdjacentWindows(opts: PrefetchOptions): void {
    const w = opts.endSec - opts.startSec
    if (w <= 0) return

    const center = (opts.startSec + opts.endSec) / 2
    // Zoom-in: same center, width max(w/2, floor). Zoom-out: same center, width w*2.
    const zoomIn = clampWindow(center, Math.max(w / 4, MIN_ZOOM_WINDOW_SEC / 2), opts.durationSec)
    const zoomOut = clampWindow(center, w, opts.durationSec)

    void warm(zoomIn, opts)
    void warm(zoomOut, opts)
}

async function warm(win: Window, opts: PrefetchOptions): Promise<void> {
    // A full-night candidate that just reproduces the current view carries no new coverage.
    const width = win.end - win.start
    const equalsCurrent =
        Math.abs(win.start - opts.startSec) < EPS && Math.abs(win.end - opts.endSec) < EPS
    if (width >= opts.durationSec - EPS && equalsCurrent) return

    const r = opts.cache.resolve(win.start, win.end, opts.durationSec)
    if (r.hit || r.fetchWindow === null) return

    const fetchWindow = r.fetchWindow
    const spansFullNight =
        fetchWindow.startSec <= EPS && fetchWindow.endSec >= opts.durationSec - EPS
    const params: WaveformDataParams = { max_points: fetchWindow.maxPoints }
    if (!spansFullNight) {
        params.start_seconds = fetchWindow.startSec
        params.end_seconds = fetchWindow.endSec
    }

    try {
        const response = await getWaveformData(
            opts.sessionId,
            opts.waveformType,
            params,
            opts.signal,
        )
        opts.cache.store(fetchWindow, response, opts.durationSec)
    } catch {
        // Swallow all errors (abort included): prefetch must never surface state or store bad data.
    }
}
