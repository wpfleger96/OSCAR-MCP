import { apiGet, apiPost } from './client'
import type { AuthStatusResponse } from '@/types'
import type { components } from '@/types/generated'

type LoginRequest = components['schemas']['LoginRequest']
type MessageResponse = components['schemas']['MessageResponse']
type ActiveProfileRequest = components['schemas']['ActiveProfileRequest']
type InviteLookupRequest = components['schemas']['InviteLookupRequest']
type InviteInfoResponse = components['schemas']['InviteInfoResponse']
type InviteRedeemRequest = components['schemas']['InviteRedeemRequest']

export const getAuthStatus = apiGet<AuthStatusResponse>('/auth/status')

export const loginUser = apiPost<MessageResponse, [body: LoginRequest]>('/auth/login', (body) => ({
    data: body,
}))

export const logoutUser = apiPost<MessageResponse>('/auth/logout')

export const switchProfile = apiPost<MessageResponse, [body: ActiveProfileRequest]>(
    '/auth/active-profile',
    (body) => ({ data: body }),
)

export const lookupInvite = apiPost<InviteInfoResponse, [body: InviteLookupRequest]>(
    '/auth/invites/lookup',
    (body) => ({ data: body }),
)

export const redeemInvite = apiPost<MessageResponse, [body: InviteRedeemRequest]>(
    '/auth/invites/redeem',
    (body) => ({ data: body }),
)
