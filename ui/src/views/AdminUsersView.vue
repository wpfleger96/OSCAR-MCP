<template>
    <div class="admin-users-view">
        <h1 class="page-title">User Management</h1>

        <!-- Users section -->
        <div class="section-card">
            <h2>Users</h2>
            <div v-if="usersLoading" class="loading-state">
                <Loader2 class="h-5 w-5 animate-spin" />
                <span>Loading users…</span>
            </div>
            <p v-else-if="usersError" role="alert" class="section-error">{{ usersError }}</p>
            <template v-else>
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Email</TableHead>
                            <TableHead>Display name</TableHead>
                            <TableHead style="width: 110px">Role</TableHead>
                            <TableHead style="width: 90px">Status</TableHead>
                            <TableHead style="width: 100px">Actions</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <template v-for="u in usersData ?? []" :key="u.id">
                            <TableRow>
                                <TableCell>{{ u.email }}</TableCell>
                                <TableCell>
                                    <template v-if="editingUserId === u.id">
                                        <div class="inline-edit">
                                            <input
                                                v-model="editName"
                                                class="edit-input"
                                                :disabled="editNameSaving"
                                                @keydown.enter.prevent="saveDisplayName(u.id)"
                                                @keydown.escape.prevent="cancelEditName"
                                            />
                                            <button
                                                class="action-btn"
                                                :disabled="editNameSaving"
                                                @click="saveDisplayName(u.id)"
                                            >
                                                Save
                                            </button>
                                            <button
                                                class="action-btn action-btn--ghost"
                                                :disabled="editNameSaving"
                                                @click="cancelEditName"
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </template>
                                    <template v-else>
                                        <span class="editable-cell" @click="startEditName(u)">
                                            {{ u.display_name ?? '—' }}
                                        </span>
                                    </template>
                                </TableCell>
                                <TableCell>
                                    <select
                                        :value="displayedRoles[u.id] ?? u.role"
                                        class="role-select"
                                        @change="
                                            onRoleChange(
                                                u,
                                                ($event.target as HTMLSelectElement).value,
                                            )
                                        "
                                    >
                                        <option value="admin">admin</option>
                                        <option value="member">member</option>
                                        <option value="demo">demo</option>
                                    </select>
                                </TableCell>
                                <TableCell>
                                    <span
                                        v-if="!u.disabled"
                                        class="status-badge status-badge--active"
                                        >Active</span
                                    >
                                    <span v-else class="status-badge status-badge--disabled"
                                        >Disabled</span
                                    >
                                </TableCell>
                                <TableCell>
                                    <button
                                        v-if="!u.disabled && u.id !== currentUser?.id"
                                        class="action-btn action-btn--destructive"
                                        @click="handleDisable(u.id)"
                                    >
                                        Disable
                                    </button>
                                    <button
                                        v-if="u.disabled"
                                        class="action-btn"
                                        @click="handleEnable(u.id)"
                                    >
                                        Enable
                                    </button>
                                </TableCell>
                            </TableRow>
                            <TableRow v-if="userRowErrors[u.id]">
                                <TableCell :colspan="5" class="error-cell">
                                    <p role="alert" class="row-error">{{ userRowErrors[u.id] }}</p>
                                </TableCell>
                            </TableRow>
                        </template>
                    </TableBody>
                </Table>
            </template>
        </div>

        <!-- Create invite section -->
        <div class="section-card">
            <h2>Create invite</h2>
            <template v-if="!createdInvite">
                <form class="invite-form" @submit.prevent="handleCreateInvite">
                    <input
                        v-model="inviteEmail"
                        type="email"
                        placeholder="Email"
                        class="field-input"
                        required
                        :disabled="creatingInvite"
                    />
                    <select v-model="inviteRole" class="field-select" :disabled="creatingInvite">
                        <option value="member">Member</option>
                        <option value="admin">Admin</option>
                    </select>
                    <div class="ttl-group">
                        <label class="field-label" for="ttl-days">Expires in</label>
                        <input
                            id="ttl-days"
                            v-model.number="inviteTtlDays"
                            type="number"
                            min="1"
                            max="30"
                            class="field-input ttl-input"
                            :disabled="creatingInvite"
                        />
                        <span class="field-unit">days</span>
                    </div>
                    <Button type="submit" :disabled="creatingInvite">
                        <Loader2 v-if="creatingInvite" class="h-4 w-4 animate-spin mr-2" />
                        Send invite
                    </Button>
                </form>
                <p v-if="createInviteError" role="alert" class="section-error">
                    {{ createInviteError }}
                </p>
            </template>
            <template v-else>
                <p class="invite-caption">
                    Send this URL to the invitee — it will not be shown again.
                </p>
                <div class="invite-url-row">
                    <input
                        ref="inviteUrlInputRef"
                        :value="createdInvite.invite_url"
                        readonly
                        class="field-input invite-url-input"
                    />
                    <Button variant="outline" type="button" @click="copyInviteUrl">Copy</Button>
                </div>
                <Button variant="outline" class="mt-3" type="button" @click="resetInviteForm">
                    Create another
                </Button>
            </template>
        </div>

        <!-- Pending invites section -->
        <div class="section-card">
            <h2>Pending invites</h2>
            <div v-if="invitesLoading" class="loading-state">
                <Loader2 class="h-5 w-5 animate-spin" />
                <span>Loading invites…</span>
            </div>
            <p v-else-if="invitesError" role="alert" class="section-error">{{ invitesError }}</p>
            <template v-else>
                <p v-if="!invitesData?.length" class="empty-state">No pending invites.</p>
                <Table v-else>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Email</TableHead>
                            <TableHead style="width: 90px">Role</TableHead>
                            <TableHead style="width: 120px">Created</TableHead>
                            <TableHead style="width: 120px">Expires</TableHead>
                            <TableHead style="width: 150px">Action</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <template v-for="inv in invitesData" :key="inv.id">
                            <TableRow>
                                <TableCell>{{ inv.email }}</TableCell>
                                <TableCell>{{ inv.role }}</TableCell>
                                <TableCell>{{ formatDate(inv.created_at) }}</TableCell>
                                <TableCell>{{ formatDate(inv.expires_at) }}</TableCell>
                                <TableCell>
                                    <template v-if="revokingInviteId === inv.id">
                                        <span class="revoke-confirm-label">Revoke?</span>
                                        <button
                                            class="action-btn action-btn--destructive"
                                            @click="executeRevoke(inv.id)"
                                        >
                                            Yes
                                        </button>
                                        <button
                                            class="action-btn action-btn--ghost"
                                            @click="revokingInviteId = null"
                                        >
                                            No
                                        </button>
                                    </template>
                                    <button
                                        v-else
                                        class="action-btn action-btn--ghost"
                                        @click="revokingInviteId = inv.id"
                                    >
                                        Revoke
                                    </button>
                                </TableCell>
                            </TableRow>
                            <TableRow v-if="inviteRowErrors[inv.id]">
                                <TableCell :colspan="5" class="error-cell">
                                    <p role="alert" class="row-error">
                                        {{ inviteRowErrors[inv.id] }}
                                    </p>
                                </TableCell>
                            </TableRow>
                        </template>
                    </TableBody>
                </Table>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import { Loader2 } from '@lucide/vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { useApiLoad } from '@/composables/useApiLoad'
import { useAuth } from '@/composables/useAuth'
import {
    listUsers,
    updateUser,
    disableUser,
    enableUser,
    listInvites,
    createInvite,
    revokeInvite,
} from '@/api/admin'
import type { components } from '@/types/generated'

type UserItem = components['schemas']['UserItem']
type InviteCreatedResponse = components['schemas']['InviteCreatedResponse']

const { user: currentUser } = useAuth()

// --- Users ---

const {
    data: usersData,
    loading: usersLoading,
    error: usersError,
    reload: reloadUsers,
} = useApiLoad(listUsers, 'Failed to load users')

// Track displayed role per user so the select reverts on error without a full reload.
const displayedRoles = reactive<Record<number, string | undefined>>({})
const userRowErrors = reactive<Record<number, string | undefined>>({})

watch(usersData, (users) => {
    if (!users) return
    for (const u of users) {
        displayedRoles[u.id] = u.role
        delete userRowErrors[u.id]
    }
})

const editingUserId = ref<number | null>(null)
const editName = ref('')
const editNameSaving = ref(false)

function startEditName(u: UserItem): void {
    editingUserId.value = u.id
    editName.value = u.display_name ?? ''
    delete userRowErrors[u.id]
}

function cancelEditName(): void {
    editingUserId.value = null
    editName.value = ''
}

async function saveDisplayName(userId: number): Promise<void> {
    editNameSaving.value = true
    delete userRowErrors[userId]
    try {
        const name = editName.value.trim()
        await updateUser(userId, { display_name: name || null })
        cancelEditName()
        await reloadUsers()
    } catch (e: unknown) {
        userRowErrors[userId] = e instanceof Error ? e.message : 'Failed to update display name'
    } finally {
        editNameSaving.value = false
    }
}

async function onRoleChange(u: UserItem, newRole: string): Promise<void> {
    const oldRole = displayedRoles[u.id] ?? u.role
    displayedRoles[u.id] = newRole
    delete userRowErrors[u.id]
    try {
        await updateUser(u.id, { role: newRole as 'admin' | 'member' | 'demo' })
        await reloadUsers()
    } catch (e: unknown) {
        displayedRoles[u.id] = oldRole
        userRowErrors[u.id] = e instanceof Error ? e.message : 'Failed to update role'
    }
}

async function handleDisable(userId: number): Promise<void> {
    delete userRowErrors[userId]
    try {
        await disableUser(userId)
        await reloadUsers()
    } catch (e: unknown) {
        userRowErrors[userId] = e instanceof Error ? e.message : 'Failed to disable user'
    }
}

async function handleEnable(userId: number): Promise<void> {
    delete userRowErrors[userId]
    try {
        await enableUser(userId)
        await reloadUsers()
    } catch (e: unknown) {
        userRowErrors[userId] = e instanceof Error ? e.message : 'Failed to enable user'
    }
}

// --- Create invite ---

const inviteEmail = ref('')
const inviteRole = ref<'admin' | 'member'>('member')
const inviteTtlDays = ref(7)
const creatingInvite = ref(false)
const createInviteError = ref<string | null>(null)
const createdInvite = ref<InviteCreatedResponse | null>(null)
const inviteUrlInputRef = ref<HTMLInputElement | null>(null)

async function handleCreateInvite(): Promise<void> {
    creatingInvite.value = true
    createInviteError.value = null
    try {
        createdInvite.value = await createInvite({
            email: inviteEmail.value.trim(),
            role: inviteRole.value,
            ttl_days: inviteTtlDays.value,
        })
    } catch (e: unknown) {
        createInviteError.value = e instanceof Error ? e.message : 'Failed to create invite'
    } finally {
        creatingInvite.value = false
    }
}

async function copyInviteUrl(): Promise<void> {
    if (!createdInvite.value) return
    try {
        await navigator.clipboard.writeText(createdInvite.value.invite_url)
    } catch {
        inviteUrlInputRef.value?.select()
    }
}

function resetInviteForm(): void {
    createdInvite.value = null
    inviteEmail.value = ''
    inviteRole.value = 'member'
    inviteTtlDays.value = 7
    createInviteError.value = null
}

// --- Pending invites ---

const {
    data: invitesData,
    loading: invitesLoading,
    error: invitesError,
    reload: reloadInvites,
} = useApiLoad(listInvites, 'Failed to load invites')

const revokingInviteId = ref<number | null>(null)
const inviteRowErrors = reactive<Record<number, string | undefined>>({})

async function executeRevoke(inviteId: number): Promise<void> {
    delete inviteRowErrors[inviteId]
    try {
        await revokeInvite(inviteId)
        revokingInviteId.value = null
        await reloadInvites()
    } catch (e: unknown) {
        revokingInviteId.value = null
        inviteRowErrors[inviteId] = e instanceof Error ? e.message : 'Failed to revoke invite'
    }
}

// --- Utilities ---

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}
</script>

<style scoped>
.admin-users-view {
    max-width: 960px;
    margin: 0 auto;
    padding: 1.5rem;
}

/* Status badges */
.status-badge {
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.125rem 0.5rem;
    border-radius: 999px;
    display: inline-block;
}

.status-badge--active {
    color: var(--color-primary);
    background: hsl(from var(--color-primary) h s l / 0.1);
}

.status-badge--disabled {
    color: var(--color-muted-foreground);
    background: var(--color-accent);
}

/* Inline display-name editing */
.inline-edit {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}

.edit-input {
    height: 1.875rem;
    border: 1px solid var(--color-input);
    border-radius: 4px;
    padding: 0 0.5rem;
    font-size: 0.875rem;
    background: transparent;
    color: var(--color-foreground);
    outline: none;
    min-width: 0;
    flex: 1;
}

.edit-input:focus {
    border-color: var(--color-primary);
}

.editable-cell {
    cursor: pointer;
}

.editable-cell:hover {
    text-decoration: underline;
    text-underline-offset: 2px;
}

/* Role select */
.role-select {
    height: 1.875rem;
    border: 1px solid var(--color-input);
    border-radius: 4px;
    padding: 0 0.375rem;
    font-size: 0.875rem;
    background: transparent;
    color: var(--color-foreground);
    outline: none;
    cursor: pointer;
    width: 100%;
}

.role-select:focus {
    border-color: var(--color-primary);
}

/* Action buttons */
.action-btn {
    font-size: 0.8rem;
    padding: 0.25rem 0.625rem;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-card);
    color: var(--color-foreground);
    cursor: pointer;
    transition: background 0.1s;
    margin-right: 0.25rem;
}

.action-btn:last-child {
    margin-right: 0;
}

.action-btn:hover:not(:disabled) {
    background: var(--color-accent);
}

.action-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.action-btn--ghost {
    background: transparent;
    border-color: transparent;
}

.action-btn--ghost:hover:not(:disabled) {
    background: var(--color-accent);
    border-color: var(--color-border);
}

.action-btn--destructive {
    color: var(--color-destructive);
    border-color: color-mix(in srgb, var(--color-destructive) 40%, transparent);
}

.action-btn--destructive:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-destructive) 10%, transparent);
}

/* Error display */
.section-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin-top: 0.5rem;
}

.error-cell {
    padding-top: 0;
    padding-bottom: 0.25rem;
}

.row-error {
    font-size: 0.8125rem;
    color: var(--color-destructive);
    margin: 0;
}

.empty-state {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    padding: 0.5rem 0;
}

/* Create invite form */
.invite-form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    align-items: center;
}

.field-input {
    height: 2.25rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0 0.75rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    outline: none;
    transition: border-color 0.15s;
}

.field-input:focus {
    border-color: var(--color-primary);
}

.field-input:disabled {
    opacity: 0.6;
}

.field-select {
    height: 2.25rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0 0.5rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    outline: none;
}

.field-select:focus {
    border-color: var(--color-primary);
}

.field-select:disabled {
    opacity: 0.6;
}

.ttl-group {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}

.field-label {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    white-space: nowrap;
}

.ttl-input {
    width: 4.5rem;
}

.field-unit {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

/* Invite URL display */
.invite-caption {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin-bottom: 0.75rem;
}

.invite-url-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.25rem;
}

.invite-url-input {
    flex: 1;
    font-family: monospace;
    font-size: 0.8125rem;
    min-width: 0;
}

.mt-3 {
    margin-top: 0.75rem;
}

/* Revoke confirm */
.revoke-confirm-label {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
    margin-right: 0.25rem;
}
</style>
