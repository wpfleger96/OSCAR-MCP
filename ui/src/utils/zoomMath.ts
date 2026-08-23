/**
 * Shared zoom-step geometry for the session-detail waveform charts.
 *
 * The zoom buttons (SessionDetailView) and the background prefetcher (waveformPrefetch) must
 * agree on the EXACT windows a zoom step produces: the prefetcher only warms the right chunk if
 * it predicts the button's window byte-for-byte, so any drift silently kills the hit rate. These
 * helpers are the single source of that geometry.
 */
import { MIN_ZOOM_WINDOW_SEC } from '@/constants/waveform'

export interface ZoomWindow {
    start: number
    end: number
}

/** Clamp a centered window into [0, duration], edge-shifting to preserve the desired width. */
export function clampZoomWindow(center: number, halfWidth: number, duration: number): ZoomWindow {
    let start = Math.max(0, center - halfWidth)
    let end = Math.min(duration, center + halfWidth)
    const desired = halfWidth * 2
    if (start === 0) end = Math.min(duration, desired)
    else if (end === duration) start = Math.max(0, duration - desired)
    return { start, end }
}

/** The window the zoom-in button produces from [start, end] (half-width floored at the 5 s max-zoom). */
export function zoomInWindow(start: number, end: number, duration: number): ZoomWindow {
    const center = (start + end) / 2
    const halfWidth = Math.max(MIN_ZOOM_WINDOW_SEC / 2, (end - start) / 4)
    return clampZoomWindow(center, halfWidth, duration)
}

/** The window the zoom-out button produces from [start, end] (doubled width, clamped). */
export function zoomOutWindow(start: number, end: number, duration: number): ZoomWindow {
    const center = (start + end) / 2
    return clampZoomWindow(center, end - start, duration)
}
