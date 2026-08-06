import { apiGet, apiPatch, apiPost } from './client'
import type { components } from '@/types/generated'

type MeResponse = components['schemas']['MeResponse']
type MessageResponse = components['schemas']['MessageResponse']
type DisplayNameRequest = components['schemas']['DisplayNameRequest']
type PasswordChangeRequest = components['schemas']['PasswordChangeRequest']
type UserPreferences = components['schemas']['UserPreferences']
type UserPreferencesUpdate = components['schemas']['UserPreferencesUpdate']

export const getMe = apiGet<MeResponse>('/auth/me')

export const updateDisplayName = apiPatch<MessageResponse, [body: DisplayNameRequest]>(
    '/auth/me/display-name',
    (body) => ({ data: body }),
)

export const changePassword = apiPost<MessageResponse, [body: PasswordChangeRequest]>(
    '/auth/me/password',
    (body) => ({ data: body }),
)

export const getPreferences = apiGet<UserPreferences, [signal?: AbortSignal]>(
    '/auth/me/preferences',
    (signal) => ({ signal }),
)

export const updatePreferences = apiPatch<UserPreferences, [body: UserPreferencesUpdate]>(
    '/auth/me/preferences',
    (body) => ({ data: body }),
)
