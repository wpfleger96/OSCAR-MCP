import { ref, shallowRef, type Ref, type ShallowRef } from 'vue'
import { getWaveformData, type WaveformDataParams } from '@/api/waveforms'
import { ZOOM_FETCH_DEBOUNCE_MS } from '@/constants/waveform'
import type { WaveformCacheRegistry, FetchWindow, WaveformSlice } from '@/utils/waveformCache'

// Tolerance for treating a fetch window as spanning the full night (fractional bounds).
const EPS = 1e-6

export interface WaveformWindowComposable {
    data: ShallowRef<WaveformSlice | null>
    loading: Ref<boolean>
    error: Ref<string | null>
    loadWindow: (startSec?: number, endSec?: number) => void
    reset: () => void
}

/**
 * Cache-aware waveform window loader for the session-detail charts.
 *
 * Every request first resolves against the per-type cache in the shared registry: a hit
 * renders synchronously with zero network, a miss renders the best available slice now and
 * schedules a debounced fetch of an expanded window. Successive calls coalesce on the
 * debounce timer and the last call wins — the prior in-flight fetch is aborted so a stale
 * response never overwrites fresher data. Failed fetches keep the last-good data.
 */
export function useWaveformWindow(
    sessionId: Ref<number>,
    waveformType: Ref<string>,
    registry: WaveformCacheRegistry,
    durationSec: Ref<number>,
): WaveformWindowComposable {
    const data = shallowRef<WaveformSlice | null>(null)
    const loading = ref(false)
    const error = ref<string | null>(null)

    let abortController: AbortController | null = null
    let debounceTimer: ReturnType<typeof setTimeout> | null = null

    function cancelPending(): void {
        if (debounceTimer !== null) {
            clearTimeout(debounceTimer)
            debounceTimer = null
        }
    }

    async function runFetch(
        startSec: number,
        endSec: number,
        fetchWindow: FetchWindow,
    ): Promise<void> {
        abortController?.abort()
        const thisController = new AbortController()
        abortController = thisController

        // Capture the type at fetch time: the response belongs to this type's cache even if
        // waveformType changes before the request completes.
        const fetchedType = waveformType.value
        const spansFullNight =
            fetchWindow.startSec <= EPS && fetchWindow.endSec >= durationSec.value - EPS

        const params: WaveformDataParams = { max_points: fetchWindow.maxPoints }
        if (!spansFullNight) {
            params.start_seconds = fetchWindow.startSec
            params.end_seconds = fetchWindow.endSec
        }

        try {
            const response = await getWaveformData(
                sessionId.value,
                fetchedType,
                params,
                thisController.signal,
            )
            // Superseded by a newer load: never store, never touch state (last call wins).
            if (abortController !== thisController) return

            const cache = registry.getCache(fetchedType)
            cache.store(fetchWindow, response, durationSec.value)
            const resolved = cache.resolve(startSec, endSec, durationSec.value)
            if (resolved.slice) data.value = resolved.slice
            error.value = null
            loading.value = false
        } catch (err: unknown) {
            // Aborted request (incl. CanceledError from supersession): a newer call owns state.
            if (abortController !== thisController) return
            if (err instanceof Error && err.name !== 'CanceledError') {
                error.value = err.message
            }
            loading.value = false
        }
    }

    function loadWindow(startSec?: number, endSec?: number): void {
        const s = startSec ?? 0
        const e = endSec ?? durationSec.value
        cancelPending()

        const cache = registry.getCache(waveformType.value)
        const r = cache.resolve(s, e, durationSec.value)
        if (r.slice) data.value = r.slice

        if (r.hit || r.fetchWindow === null) {
            // Complete data in hand: supersede any in-flight fetch so it can't overwrite us.
            abortController?.abort()
            abortController = null
            error.value = null
            loading.value = false
            return
        }

        loading.value = true
        const fetchWindow = r.fetchWindow
        debounceTimer = setTimeout(() => {
            debounceTimer = null
            void runFetch(s, e, fetchWindow)
        }, ZOOM_FETCH_DEBOUNCE_MS)
    }

    function reset(): void {
        abortController?.abort()
        abortController = null
        cancelPending()
        data.value = null
        error.value = null
        loading.value = false
    }

    return { data, loading, error, loadWindow, reset }
}
