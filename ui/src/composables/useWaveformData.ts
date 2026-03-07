import { ref, shallowRef, type Ref } from 'vue'
import { getWaveformData } from '@/api/waveforms'
import type { WaveformDataResponse } from '@/types'

export interface WaveformDataComposable {
    data: ReturnType<typeof shallowRef<WaveformDataResponse | null>>
    loading: Ref<boolean>
    error: Ref<string | null>
    loadData: (startSec?: number, endSec?: number, maxPoints?: number) => Promise<void>
    reset: () => void
}

export function useWaveformData(
    sessionId: Ref<number>,
    waveformType: Ref<string>,
): WaveformDataComposable {
    const data = shallowRef<WaveformDataResponse | null>(null)
    const loading = ref(false)
    const error = ref<string | null>(null)

    let abortController: AbortController | null = null

    async function loadData(startSec?: number, endSec?: number, maxPoints = 2000): Promise<void> {
        abortController?.abort()
        abortController = new AbortController()

        loading.value = true
        error.value = null

        try {
            const params: Record<string, number> = { max_points: maxPoints }
            if (startSec !== undefined) params.start_seconds = startSec
            if (endSec !== undefined) params.end_seconds = endSec

            data.value = await getWaveformData(
                sessionId.value,
                waveformType.value,
                params,
                abortController.signal,
            )
        } catch (err: unknown) {
            if (err instanceof Error && err.name !== 'CanceledError') {
                error.value = err.message
            }
        } finally {
            loading.value = false
        }
    }

    function reset(): void {
        abortController?.abort()
        abortController = null
        data.value = null
        loading.value = false
        error.value = null
    }

    return { data, loading, error, loadData, reset }
}
