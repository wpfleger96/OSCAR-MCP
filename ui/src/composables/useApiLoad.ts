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
 */
export function useApiLoad<T>(
    fetcher: () => Promise<T>,
    errorMessage = 'Request failed',
): ApiLoad<T> {
    const data = shallowRef<T | null>(null)
    const loading = ref(true)
    const error = ref<string | null>(null)

    async function reload(): Promise<void> {
        loading.value = true
        error.value = null
        try {
            data.value = await fetcher()
        } catch (err: unknown) {
            error.value = err instanceof Error ? err.message : errorMessage
        } finally {
            loading.value = false
        }
    }

    onMounted(() => void reload())

    return { data, loading, error, reload }
}
