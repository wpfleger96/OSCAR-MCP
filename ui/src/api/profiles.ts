import { apiGet, apiPatch, apiPost } from './client'
import type { ProfileResponse } from '@/types'
import type { components } from '@/types/generated'

type CreateProfileRequest = components['schemas']['CreateProfileRequest']
type RenameProfileRequest = components['schemas']['RenameProfileRequest']

export const listProfiles = apiGet<ProfileResponse[]>('/profiles/')

export const createProfile = apiPost<ProfileResponse, [body: CreateProfileRequest]>(
    '/profiles/',
    (body) => ({ data: body }),
)

export const updateProfile = apiPatch<
    ProfileResponse,
    [profileId: number, body: RenameProfileRequest]
>(
    (profileId) => `/profiles/${profileId}`,
    (_profileId, body) => ({ data: body }),
)

export const setDefaultProfile = (profileId: number): Promise<ProfileResponse> =>
    updateProfile(profileId, { default: true })
