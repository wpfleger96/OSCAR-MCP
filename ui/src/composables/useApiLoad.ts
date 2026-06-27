import { onMounted, ref, shallowRef } from 'vue'
import type { Ref, ShallowRef } from 'vue'

export interface ApiLoad<T> {
    data: ShallowRef<T | null>
    loading: Ref<boolean>
    error: Ref<string | null>
    reload: () => Promise<void>
}

/**
 * Wrap an async fetcher with loading/error state.
 *
 * The fetcher runs on component mount; call reload() to re-fetch (e.g. from
 * a filter watcher). `data` keeps its previous value while a reload is in
 * flight, and `error` falls back to `errorMessage` for non-Error rejections.
 * Concurrent calls are serialized by sequence number — only the last response wins.
 */
export function useApiLoad<T>(
    fetcher: () => Promise<T>,
    errorMessage = 'Request failed',
): ApiLoad<T> {
    const data = shallowRef<T | null>(null)
    const loading = ref(true)
    const error = ref<string | null>(null)
    let seq = 0

    async function reload(): Promise<void> {
        const thisSeq = ++seq
        loading.value = true
        error.value = null
        try {
            const result = await fetcher()
            if (thisSeq === seq) data.value = result
        } catch (err: unknown) {
            if (thisSeq === seq) error.value = err instanceof Error ? err.message : errorMessage
        } finally {
            if (thisSeq === seq) loading.value = false
        }
    }

    onMounted(() => void reload())

    return { data, loading, error, reload }
}
