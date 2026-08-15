import { apiDelete, apiGet, apiPost } from './client'
import type { components } from '@/types/generated'

type TotpStatusResponse = components['schemas']['TotpStatusResponse']
type TotpSetupResponse = components['schemas']['TotpSetupResponse']
type TotpConfirmRequest = components['schemas']['TotpConfirmRequest']
type TotpDisableRequest = components['schemas']['TotpDisableRequest']
type TotpRegenerateRequest = components['schemas']['TotpRegenerateRequest']
type RecoveryCodesResponse = components['schemas']['RecoveryCodesResponse']
type MessageResponse = components['schemas']['MessageResponse']

export const getTotpStatus = apiGet<TotpStatusResponse>('/auth/me/totp')

export const setupTotp = apiPost<TotpSetupResponse>('/auth/me/totp/setup')

export const confirmTotp = apiPost<RecoveryCodesResponse, [body: TotpConfirmRequest]>(
    '/auth/me/totp/confirm',
    (body) => ({ data: body }),
)

// DELETE with a JSON body — axios supports this via the `data` config key.
export const disableTotp = apiDelete<MessageResponse, [body: TotpDisableRequest]>(
    '/auth/me/totp',
    (body) => ({ data: body }),
)

export const regenerateRecoveryCodes = apiPost<
    RecoveryCodesResponse,
    [body: TotpRegenerateRequest]
>('/auth/me/totp/recovery-codes/regenerate', (body) => ({ data: body }))

export const adminResetTotp = apiPost<MessageResponse, [userId: number, code?: string]>(
    (userId) => `/admin/users/${userId}/totp/reset`,
    (_userId, code) => (code !== undefined ? { data: { code } } : {}),
)
