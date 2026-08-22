/**
 * Background warming of the cache for the moves the user is most likely to make next. After a
 * zoom settles we warm three targets and fetch the misses in the background so the next move
 * resolves as a hit whose first render is already final:
 *
 *   1. Zoom-in step  — one button press narrower (same math as SessionDetailView's zoom-in).
 *   2. Zoom-out step — one button press wider (same math as SessionDetailView's zoom-out).
 *   3. Drag headroom — a full-density copy of the CURRENT view. A drag jumps to a sub-window of
 *      the current view (typically 5-20× narrower) that the ±2× headroom of an ordinary chunk
 *      cannot serve; probe a 1/10-width center window and, on a miss, fetch the current window
 *      un-expanded at native density so any deep drag inside it lands as an instant hit.
 *
 * Fire-and-forget: every error (abort included) is swallowed and no component state is ever touched.
 */
import { getWaveformData } from '@/api/waveforms'
import { MIN_ZOOM_WINDOW_SEC, SERVER_MAX_POINTS } from '@/constants/waveform'
import { buildWaveformParams, type FetchWindow, type WaveformWindowCache } from './waveformCache'
import { clampZoomWindow, zoomInWindow, zoomOutWindow, type ZoomWindow } from './zoomMath'

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

/** Fire-and-forget: warm the cache for the next zoom-in/out steps and for a drag inside the view. */
export function prefetchAdjacentWindows(opts: PrefetchOptions): void {
    const w = opts.endSec - opts.startSec
    if (w <= 0) return

    void warm(zoomInWindow(opts.startSec, opts.endSec, opts.durationSec), opts)
    void warm(zoomOutWindow(opts.startSec, opts.endSec, opts.durationSec), opts)
    void warmHeadroom(opts)
}

async function warm(win: ZoomWindow, opts: PrefetchOptions): Promise<void> {
    // A full-night candidate that just reproduces the current view carries no new coverage.
    const width = win.end - win.start
    const equalsCurrent =
        Math.abs(win.start - opts.startSec) < EPS && Math.abs(win.end - opts.endSec) < EPS
    if (width >= opts.durationSec - EPS && equalsCurrent) return

    const r = opts.cache.resolve(win.start, win.end, opts.durationSec)
    if (r.hit || r.fetchWindow === null) return

    const fetchWindow = r.fetchWindow
    try {
        const response = await getWaveformData(
            opts.sessionId,
            opts.waveformType,
            buildWaveformParams(fetchWindow, opts.durationSec),
            opts.signal,
        )
        opts.cache.store(fetchWindow, response, opts.durationSec)
    } catch {
        // Swallow all errors (abort included): prefetch must never surface state or store bad data.
    }
}

/**
 * Warm a full-density copy of the current view so drags into it hit. Probe a narrow center window
 * (1/10 the view width): a hit means headroom already exists, so do nothing. On a miss, fetch the
 * CURRENT window un-expanded at native density — once SERVER_MAX_POINTS covers it exactly, every
 * deeper drag inside the view is a permanent hit. The probe's own fetchWindow is ignored.
 */
async function warmHeadroom(opts: PrefetchOptions): Promise<void> {
    const w = opts.endSec - opts.startSec
    const center = (opts.startSec + opts.endSec) / 2
    const probeHalf = Math.max(w / 10, MIN_ZOOM_WINDOW_SEC) / 2
    const probe = clampZoomWindow(center, probeHalf, opts.durationSec)
    if (opts.cache.resolve(probe.start, probe.end, opts.durationSec).hit) return

    const fetchWindow: FetchWindow = {
        startSec: opts.startSec,
        endSec: opts.endSec,
        maxPoints: SERVER_MAX_POINTS,
    }
    try {
        const response = await getWaveformData(
            opts.sessionId,
            opts.waveformType,
            buildWaveformParams(fetchWindow, opts.durationSec),
            opts.signal,
        )
        opts.cache.store(fetchWindow, response, opts.durationSec)
    } catch {
        // Swallow all errors (abort included): prefetch must never surface state or store bad data.
    }
}
