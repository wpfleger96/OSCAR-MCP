import { apiPost } from './client'
import api from './client'
import type { ImportSource, ImportResult, ImportPathRequest } from '@/types'
import type { AxiosProgressEvent } from 'axios'

export interface FileEntry {
    file: File
    path: string
}

export const detectSources = apiPost<ImportSource[], [body: { path: string }]>(
    '/import/detect',
    (body) => ({ data: body }),
)

export const importFromPath = apiPost<ImportResult, [body: ImportPathRequest]>(
    '/import/path',
    (body) => ({ data: body }),
)

export async function importFiles(
    entries: FileEntry[],
    onProgress?: (event: AxiosProgressEvent) => void,
): Promise<ImportResult> {
    const formData = new FormData()
    for (const entry of entries) {
        formData.append('files', entry.file, entry.path)
    }
    const { data } = await api.post<ImportResult>('/import/', formData, {
        onUploadProgress: onProgress,
    })
    return data
}
