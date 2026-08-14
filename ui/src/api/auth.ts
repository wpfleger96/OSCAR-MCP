import { apiGet, apiPost } from './client'
import type { AuthStatusResponse } from '@/types'
import type { components } from '@/types/generated'

type LoginRequest = components['schemas']['LoginRequest']
type LoginResponse = components['schemas']['LoginResponse']
type MessageResponse = components['schemas']['MessageResponse']
type TotpChallengeRequest = components['schemas']['TotpChallengeRequest']
type ActiveProfileRequest = components['schemas']['ActiveProfileRequest']
type InviteLookupRequest = components['schemas']['InviteLookupRequest']
type InviteInfoResponse = components['schemas']['InviteInfoResponse']
type InviteRedeemRequest = components['schemas']['InviteRedeemRequest']

// POST /auth/invites/google — token in body (never in URL path).
// Returns an authorization URL to redirect the browser to.
interface GoogleInviteRequest {
    token: string
}
interface GoogleInviteResponse {
    authorization_url: string
}

export const getAuthStatus = apiGet<AuthStatusResponse, [signal?: AbortSignal]>(
    '/auth/status',
    (signal) => ({ signal }),
)

export const loginUser = apiPost<LoginResponse, [body: LoginRequest]>('/auth/login', (body) => ({
    data: body,
}))

export const submitTotpChallenge = apiPost<MessageResponse, [body: TotpChallengeRequest]>(
    '/auth/login/totp',
    (body) => ({ data: body }),
)

export const demoLoginUser = apiPost<MessageResponse>('/auth/demo-login')

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

export const initiateGoogleInvite = apiPost<GoogleInviteResponse, [body: GoogleInviteRequest]>(
    '/auth/invites/google',
    (body) => ({ data: body }),
)
