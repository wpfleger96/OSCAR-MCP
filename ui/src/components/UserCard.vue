<template>
    <div class="data-card">
        <div class="data-card-header">
            <span class="card-email">{{ user.email }}</span>
            <span v-if="!user.disabled" class="status-badge status-badge--active">Active</span>
            <span v-else class="status-badge status-badge--disabled">Disabled</span>
        </div>
        <div v-if="editing" class="inline-edit card-inline-edit">
            <input
                :value="editName"
                class="edit-input"
                :disabled="editNameSaving"
                @input="$emit('update:editName', ($event.target as HTMLInputElement).value)"
                @keydown.enter.prevent="$emit('saveName')"
                @keydown.escape.prevent="$emit('cancelEdit')"
            />
            <button class="action-btn" :disabled="editNameSaving" @click="$emit('saveName')">
                Save
            </button>
            <button
                class="action-btn action-btn--ghost"
                :disabled="editNameSaving"
                @click="$emit('cancelEdit')"
            >
                Cancel
            </button>
        </div>
        <div v-else class="data-card-row">
            <span class="data-card-label">Name</span>
            <span class="editable-cell data-card-value" @click="$emit('startEdit')">
                {{ user.display_name ?? '—' }}
            </span>
        </div>
        <div class="data-card-row">
            <span class="data-card-label">Role</span>
            <select
                :value="displayedRole"
                class="role-select"
                :disabled="busy"
                @change="$emit('roleChange', ($event.target as HTMLSelectElement).value)"
            >
                <option value="admin">admin</option>
                <option value="member">member</option>
                <option value="demo">demo</option>
            </select>
        </div>
        <div class="data-card-row">
            <span class="data-card-label">Auth</span>
            <span class="data-card-value">
                <template v-if="user.has_password || user.auth_providers.length > 0">
                    <span v-if="user.has_password" class="status-badge status-badge--neutral"
                        >Password</span
                    >
                    <span
                        v-for="provider in user.auth_providers"
                        :key="provider"
                        class="status-badge status-badge--neutral"
                        >{{ provider.charAt(0).toUpperCase() + provider.slice(1) }}</span
                    >
                </template>
                <span
                    v-else
                    class="status-badge status-badge--neutral"
                    title="No login method — user cannot sign in"
                    >None</span
                >
                <span
                    v-if="user.totp_enabled"
                    class="status-badge status-badge--totp"
                    title="Two-factor authentication enabled"
                    >2FA</span
                >
            </span>
        </div>
        <div class="data-card-row">
            <span class="data-card-label">Last login</span>
            <span v-if="lastLogin" class="data-card-value">{{ lastLogin }}</span>
            <span v-else class="data-card-value muted-text">Never</span>
        </div>
        <div v-if="user.disabled || !isCurrentUser || user.totp_enabled" class="data-card-actions">
            <button
                v-if="!user.disabled && !isCurrentUser"
                class="action-btn action-btn--destructive"
                :disabled="busy"
                @click="$emit('disable')"
            >
                Disable
            </button>
            <button
                v-if="user.disabled"
                class="action-btn"
                :disabled="busy"
                @click="$emit('enable')"
            >
                Enable
            </button>
            <template v-if="user.totp_enabled">
                <div v-if="totpResetConfirming" class="totp-reset-confirm">
                    <span class="totp-reset-confirm-label">Reset 2FA?</span>
                    <input
                        v-if="adminHasTotp"
                        :value="totpResetCode"
                        type="text"
                        inputmode="numeric"
                        pattern="[0-9]{6}"
                        maxlength="6"
                        placeholder="Your code"
                        autocomplete="one-time-code"
                        class="totp-reset-code-input"
                        :disabled="busy"
                        @input="
                            $emit('update:totpResetCode', ($event.target as HTMLInputElement).value)
                        "
                    />
                    <div class="totp-reset-confirm-buttons">
                        <button
                            class="action-btn action-btn--destructive"
                            :disabled="busy || (adminHasTotp && !totpResetCode)"
                            @click="$emit('confirmTotpReset')"
                        >
                            Yes
                        </button>
                        <button
                            class="action-btn action-btn--ghost"
                            :disabled="busy"
                            @click="$emit('cancelTotpReset')"
                        >
                            No
                        </button>
                    </div>
                </div>
                <button
                    v-else
                    class="action-btn action-btn--ghost"
                    :disabled="busy"
                    @click="$emit('startTotpReset')"
                >
                    Reset 2FA
                </button>
            </template>
        </div>
        <p v-if="error" role="alert" class="row-error card-row-error">{{ error }}</p>
    </div>
</template>

<script setup lang="ts">
import type { components } from '@/types/generated'

type UserItem = components['schemas']['UserItem']

defineProps<{
    user: UserItem
    isCurrentUser: boolean
    displayedRole: string
    busy: boolean
    editing: boolean
    editName: string
    editNameSaving: boolean
    lastLogin: string | null
    adminHasTotp: boolean
    totpResetConfirming: boolean
    totpResetCode: string
    error?: string
}>()

defineEmits<{
    startEdit: []
    'update:editName': [value: string]
    saveName: []
    cancelEdit: []
    roleChange: [role: string]
    disable: []
    enable: []
    startTotpReset: []
    confirmTotpReset: []
    cancelTotpReset: []
    'update:totpResetCode': [value: string]
}>()
</script>

<style scoped>
.data-card-header .status-badge {
    flex-shrink: 0;
}

.card-email {
    min-width: 0;
    overflow-wrap: anywhere;
}

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

.status-badge--neutral {
    color: var(--color-foreground);
    background: var(--color-accent);
    margin-right: 0.25rem;
}

.status-badge--neutral:last-child {
    margin-right: 0;
}

.status-badge--totp {
    color: var(--color-primary);
    background: hsl(from var(--color-primary) h s l / 0.12);
    margin-left: 0.25rem;
}

.muted-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

/* Inline display-name editing */
.inline-edit {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}

.card-inline-edit {
    margin: 0.25rem 0 0.5rem;
}

.edit-input {
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
    border: 1px solid var(--color-input);
    border-radius: 4px;
    padding: 0 0.375rem;
    font-size: 0.875rem;
    background: transparent;
    color: var(--color-foreground);
    outline: none;
    cursor: pointer;
    min-width: 9rem;
}

.role-select:focus {
    border-color: var(--color-primary);
}

.data-card-actions {
    flex-wrap: wrap;
}

.data-card-actions .action-btn {
    flex: 1;
}

/* TOTP reset confirm sub-state: full-width stack of label, code input, Yes/No */
.totp-reset-confirm {
    flex: 1 1 100%;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.totp-reset-confirm-label {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
}

.totp-reset-confirm-buttons {
    display: flex;
    gap: 0.5rem;
}

.totp-reset-code-input {
    border: 1px solid var(--color-input);
    border-radius: 4px;
    padding: 0 0.5rem;
    font-size: 0.875rem;
    background: transparent;
    color: var(--color-foreground);
    outline: none;
    font-family: monospace;
    letter-spacing: 0.1em;
    width: 100%;
}

.totp-reset-code-input:focus {
    border-color: var(--color-primary);
}

/* ≥44px tap targets */
.role-select,
.action-btn,
.edit-input,
.totp-reset-code-input {
    min-height: var(--tap-target);
}

.row-error {
    font-size: 0.8125rem;
    color: var(--color-destructive);
    margin: 0;
}

.card-row-error {
    margin-top: 0.5rem;
}
</style>
