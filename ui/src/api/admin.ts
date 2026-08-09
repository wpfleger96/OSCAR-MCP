import { apiDelete, apiGet, apiPatch, apiPost } from './client'
import type { components } from '@/types/generated'

type UserItem = components['schemas']['UserItem']
type PatchUserRequest = components['schemas']['PatchUserRequest']
type InviteItem = components['schemas']['InviteItem']
type CreateInviteRequest = components['schemas']['CreateInviteRequest']
type InviteCreatedResponse = components['schemas']['InviteCreatedResponse']
type MessageResponse = components['schemas']['MessageResponse']
type McpStatus = components['schemas']['McpStatus']

export const listUsers = apiGet<UserItem[]>('/admin/users')

export const updateUser = apiPatch<MessageResponse, [userId: number, body: PatchUserRequest]>(
    (userId) => `/admin/users/${userId}`,
    (_userId, body) => ({ data: body }),
)

export const disableUser = apiPost<MessageResponse, [userId: number]>(
    (userId) => `/admin/users/${userId}/disable`,
)

export const enableUser = apiPost<MessageResponse, [userId: number]>(
    (userId) => `/admin/users/${userId}/enable`,
)

export const listInvites = apiGet<InviteItem[]>('/admin/invites')

export const createInvite = apiPost<InviteCreatedResponse, [body: CreateInviteRequest]>(
    '/admin/invites',
    (body) => ({ data: body }),
)

export const revokeInvite = apiDelete<MessageResponse, [inviteId: number]>(
    (inviteId) => `/admin/invites/${inviteId}`,
)

export const getMcpStatus = apiGet<McpStatus>('/admin/mcp/status')
