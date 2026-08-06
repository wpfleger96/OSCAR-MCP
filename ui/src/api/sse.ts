import type { ImportResult } from '@/types'

export interface ImportProgressCallbacks {
    onProgress: (data: { message: string }) => void
    onComplete: (data: {
        result: ImportResult
        analysis_job_id?: string
        analysis_queued?: boolean
    }) => void
    onError: (data: { message: string }) => void
}

/**
 * Cancel an in-progress import job.
 *
 * Sends DELETE /api/v1/import/{jobId}.  Returns true if the server accepted
 * the cancellation (204), false on any error.  Idempotent — safe to call
 * on a job that has already finished.
 */
export async function cancelImport(jobId: string): Promise<boolean> {
    try {
        const response = await fetch(`/api/v1/import/${jobId}`, {
            method: 'DELETE',
        })
        return response.status === 204
    } catch {
        return false
    }
}

export function connectImportProgress(
    jobId: string,
    callbacks: ImportProgressCallbacks,
): () => void {
    const source = new EventSource(`/api/v1/import/${jobId}/progress`)
    let done = false

    source.addEventListener('progress', (e: MessageEvent) => {
        if (done) return
        callbacks.onProgress(JSON.parse(e.data))
    })

    source.addEventListener('complete', (e: MessageEvent) => {
        if (done) return
        done = true
        callbacks.onComplete(JSON.parse(e.data))
        source.close()
    })

    // Handles both server-sent `event: error` (MessageEvent with .data) and
    // native EventSource connection errors (Event without .data).
    source.addEventListener('error', ((e: Event) => {
        if (done) return
        done = true
        if ('data' in e && (e as MessageEvent).data) {
            callbacks.onError(JSON.parse((e as MessageEvent).data))
        } else {
            callbacks.onError({ message: 'Connection lost during import' })
        }
        source.close()
    }) as EventListener)

    return () => {
        done = true
        source.close()
    }
}
