import { apiPost } from './client'
import api from './client'
import type { ImportSource, ImportPathRequest } from '@/types'
import type { AxiosProgressEvent } from 'axios'

export interface FileEntry {
    file: File
    path: string
}

interface JobResponse {
    job_id: string
}

export const detectSources = apiPost<ImportSource[], [body: { path: string }]>(
    '/import/detect',
    (body) => ({ data: body }),
)

export async function importFromPath(
    body: ImportPathRequest,
    profileId?: number,
): Promise<JobResponse> {
    const { data } = await api.post<JobResponse>(
        '/import/path',
        profileId !== undefined ? { ...body, profile_id: profileId } : body,
    )
    return data
}

export async function importFiles(
    entries: FileEntry[],
    onProgress?: (event: AxiosProgressEvent) => void,
    profileId?: number,
): Promise<JobResponse> {
    const formData = new FormData()
    for (const entry of entries) {
        formData.append('files', entry.file, entry.path)
    }
    if (profileId !== undefined) formData.append('profile_id', String(profileId))
    const { data } = await api.post<JobResponse>('/import/', formData, {
        onUploadProgress: onProgress,
    })
    return data
}
