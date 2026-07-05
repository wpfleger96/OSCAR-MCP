import type { ImportResult } from '@/types'

export interface ImportProgressCallbacks {
    onProgress: (data: { message: string }) => void
    onComplete: (data: { result: ImportResult }) => void
    onError: (data: { message: string }) => void
}

export function connectImportProgress(
    jobId: string,
    callbacks: ImportProgressCallbacks,
): () => void {
    const source = new EventSource(`/api/v1/import/${jobId}/progress`)

    source.addEventListener('progress', (e: MessageEvent) => {
        callbacks.onProgress(JSON.parse(e.data))
    })

    source.addEventListener('complete', (e: MessageEvent) => {
        callbacks.onComplete(JSON.parse(e.data))
        source.close()
    })

    source.addEventListener('error', (e: MessageEvent) => {
        if (e.data) {
            callbacks.onError(JSON.parse(e.data))
        } else {
            callbacks.onError({ message: 'Connection lost during import' })
        }
        source.close()
    })

    return () => source.close()
}
