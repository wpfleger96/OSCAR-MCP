<template>
    <div class="data-card">
        <div class="data-card-header">
            <span class="card-email">{{ invite.email }}</span>
            <span class="status-badge status-badge--neutral">{{ invite.role }}</span>
        </div>
        <div class="data-card-row">
            <span class="data-card-label">Created</span>
            <span class="data-card-value">{{ created }}</span>
        </div>
        <div class="data-card-row">
            <span class="data-card-label">Expires</span>
            <span class="data-card-value">{{ expires }}</span>
        </div>
        <div class="data-card-actions">
            <template v-if="revoking">
                <span class="revoke-confirm-label">Revoke?</span>
                <button
                    class="action-btn action-btn--destructive"
                    :disabled="revokeBusy"
                    @click="$emit('confirmRevoke')"
                >
                    Yes
                </button>
                <button
                    class="action-btn action-btn--ghost"
                    :disabled="revokeBusy"
                    @click="$emit('cancelRevoke')"
                >
                    No
                </button>
            </template>
            <button v-else class="action-btn action-btn--ghost" @click="$emit('startRevoke')">
                Revoke
            </button>
        </div>
        <p v-if="error" role="alert" class="row-error card-row-error">{{ error }}</p>
    </div>
</template>

<script setup lang="ts">
import type { components } from '@/types/generated'

type InviteItem = components['schemas']['InviteItem']

defineProps<{
    invite: InviteItem
    created: string
    expires: string
    revoking: boolean
    revokeBusy: boolean
    error?: string
}>()

defineEmits<{
    startRevoke: []
    confirmRevoke: []
    cancelRevoke: []
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

.status-badge--neutral {
    color: var(--color-foreground);
    background: var(--color-accent);
}

/* Center the confirm label beside the taller buttons */
.data-card-actions {
    align-items: center;
}

.data-card-actions .action-btn {
    flex: 1;
    min-height: var(--tap-target);
}

.revoke-confirm-label {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
    margin-right: 0.25rem;
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
