import { apiPost } from './client'
import api from './client'
import type { ImportSource, ImportResult } from '@/types'
import type { AxiosProgressEvent } from 'axios'

export const detectSources = apiPost<ImportSource[], [body: { path: string }]>(
    '/import/detect',
    (body) => ({ data: body }),
)

export async function importFiles(
    files: FileList,
    onProgress?: (event: AxiosProgressEvent) => void,
): Promise<ImportResult> {
    const formData = new FormData()
    for (const file of files) {
        formData.append('files', file, file.webkitRelativePath || file.name)
    }
    const { data } = await api.post<ImportResult>('/import/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: onProgress,
    })
    return data
}
