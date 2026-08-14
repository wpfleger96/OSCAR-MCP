<template>
    <div class="admin-mcp-view">
        <h1 class="page-title">MCP Server</h1>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-5 w-5 animate-spin" />
            <span>Loading MCP status…</span>
        </div>

        <p v-else-if="error" role="alert" class="error-state">{{ error }}</p>

        <template v-else-if="data">
            <!-- Status card -->
            <div class="section-card">
                <h2>Status</h2>
                <div class="status-row">
                    <span class="status-label">MCP server</span>
                    <span v-if="data.enabled" class="status-badge status-badge--active"
                        >Enabled</span
                    >
                    <span v-else class="status-badge status-badge--disabled">Disabled</span>
                </div>
                <p v-if="!data.enabled && data.disabled_reason" class="disabled-reason">
                    {{ data.disabled_reason }}
                </p>
            </div>

            <!-- Connection details — only shown when enabled -->
            <template v-if="data.enabled">
                <div class="section-card">
                    <h2>Connection</h2>

                    <div class="detail-row">
                        <span class="detail-label">Endpoint URL</span>
                        <div class="endpoint-row">
                            <code class="detail-value detail-value--mono">{{
                                data.endpoint_url
                            }}</code>
                            <button class="action-btn" @click="copyEndpoint">
                                {{ copied ? 'Copied!' : copyFailed ? 'Copy failed' : 'Copy' }}
                            </button>
                        </div>
                    </div>

                    <div class="detail-row">
                        <span class="detail-label">Transport</span>
                        <span class="detail-value">{{ data.transport }}</span>
                    </div>

                    <div class="detail-row">
                        <span class="detail-label">Auth provider</span>
                        <span class="detail-value">{{ data.auth_provider }}</span>
                    </div>
                </div>

                <!-- Connect a client hint -->
                <div class="section-card">
                    <h2>Connect a client</h2>
                    <p class="hint-text">
                        Paste the endpoint URL below into any MCP-compatible client (e.g. Claude
                        Desktop, Cursor) to connect to this server.
                    </p>
                    <code class="hint-url">{{ data.endpoint_url }}</code>
                </div>
            </template>

            <!-- Google identities -->
            <div class="section-card">
                <div class="bindings-header">
                    <h2>Google identities</h2>
                    <button
                        v-if="bindings && bindings.length > 0"
                        class="action-btn action-btn--destructive"
                        :disabled="!hasResettableBindings"
                        :title="
                            !hasResettableBindings ? 'No users with a password to reset' : undefined
                        "
                        @click="confirmResetAll = true"
                    >
                        Reset all
                    </button>
                </div>

                <p class="hint-text">
                    Resetting severs the link so a fresh one can form — it does not prevent
                    re-linking. To durably revoke access, disable the user account.
                </p>

                <div v-if="bindingsLoading" class="loading-state">
                    <Loader2 class="h-4 w-4 animate-spin" />
                    <span>Loading…</span>
                </div>

                <p v-else-if="bindingsError" role="alert" class="error-state">
                    {{ bindingsError }}
                </p>

                <template v-else-if="bindings">
                    <p v-if="bindings.length === 0" class="empty-state">
                        No Google accounts linked.
                    </p>

                    <table v-else class="bindings-table">
                        <thead>
                            <tr>
                                <th>User</th>
                                <th>Google account</th>
                                <th>Linked</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr
                                v-for="row in bindings"
                                :key="`${row.user_id}:${row.google_email ?? ''}:${row.linked_at}`"
                            >
                                <td>
                                    <span class="binding-email">{{ row.user_email }}</span>
                                    <span v-if="row.display_name" class="binding-name">{{
                                        row.display_name
                                    }}</span>
                                </td>
                                <td class="binding-google-email">{{ row.google_email ?? '—' }}</td>
                                <td class="binding-date">{{ formatDate(row.linked_at) }}</td>
                                <td class="binding-actions">
                                    <button
                                        class="action-btn"
                                        :disabled="!row.has_password"
                                        :title="
                                            !row.has_password
                                                ? 'No password set — resetting would remove this user’s only sign-in method.'
                                                : undefined
                                        "
                                        @click="confirmResetUserId = row.user_id"
                                    >
                                        Reset
                                    </button>
                                </td>
                            </tr>
                        </tbody>
                    </table>

                    <p v-if="resetFeedback" class="binding-feedback">{{ resetFeedback }}</p>
                    <p v-if="resetError" role="alert" class="binding-error">{{ resetError }}</p>
                </template>
            </div>
        </template>
    </div>

    <!-- Per-row reset confirm dialog -->
    <DeleteConfirmDialog
        :visible="confirmResetUserId !== null"
        title="Reset Google binding"
        :message="resetRowMessage"
        :loading="false"
        :deleting="resetting"
        confirm-label="Reset"
        @update:visible="confirmResetUserId = null"
        @confirm="handleResetBinding"
    />

    <!-- Reset-all confirm dialog -->
    <DeleteConfirmDialog
        :visible="confirmResetAll"
        title="Reset all Google bindings"
        message="Every user with a linked Google account and a password will be signed out everywhere — including you, if your account has one. Users without a password are skipped."
        :loading="false"
        :deleting="resettingAll"
        confirm-label="Reset all"
        @update:visible="confirmResetAll = $event"
        @confirm="handleResetAll"
    />
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Loader2 } from '@lucide/vue'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import { useApiLoad } from '@/composables/useApiLoad'
import { useDateFormat } from '@/composables/useDateFormat'
import {
    getMcpStatus,
    listGoogleBindings,
    resetGoogleBinding,
    resetAllGoogleBindings,
} from '@/api/admin'

const { data, loading, error } = useApiLoad(getMcpStatus, 'Failed to load MCP status')

const {
    data: bindings,
    loading: bindingsLoading,
    error: bindingsError,
    reload: reloadBindings,
} = useApiLoad(listGoogleBindings, 'Failed to load Google bindings')

const { formatDate, loadDateFormat } = useDateFormat()

onMounted(() => {
    loadDateFormat()
})

const copied = ref(false)
const copyFailed = ref(false)

async function copyEndpoint(): Promise<void> {
    if (!data.value?.endpoint_url) return
    try {
        await navigator.clipboard.writeText(data.value.endpoint_url)
        copied.value = true
        setTimeout(() => {
            copied.value = false
        }, 2000)
    } catch {
        copyFailed.value = true
        setTimeout(() => {
            copyFailed.value = false
        }, 2000)
    }
}

// ── Per-row reset ─────────────────────────────────────────────────────────────

const confirmResetUserId = ref<number | null>(null)
const resetting = ref(false)
const resetFeedback = ref<string | null>(null)
const resetError = ref<string | null>(null)

const resetRowMessage = computed(() => {
    const row = bindings.value?.find((b) => b.user_id === confirmResetUserId.value)
    const who = row ? row.user_email : 'this user'
    return (
        `This will sever ${who}'s Google sign-in and MCP access, and invalidate their sessions. ` +
        'They can re-link at their next Google sign-in (members) or via the account page (admins). ' +
        'If this is your own account you will be signed out.'
    )
})

async function handleResetBinding(): Promise<void> {
    if (confirmResetUserId.value === null) return
    const userId = confirmResetUserId.value
    confirmResetUserId.value = null
    resetting.value = true
    resetFeedback.value = null
    resetError.value = null
    try {
        await resetGoogleBinding(userId)
        await reloadBindings()
        resetFeedback.value = 'Google binding reset.'
    } catch (e: unknown) {
        resetError.value = e instanceof Error ? e.message : 'Failed to reset binding'
    } finally {
        resetting.value = false
    }
}

// True if at least one binding has a password, making a reset meaningful.
const hasResettableBindings = computed(() => bindings.value?.some((b) => b.has_password) ?? false)

// ── Reset all ─────────────────────────────────────────────────────────────────

const confirmResetAll = ref(false)
const resettingAll = ref(false)

async function handleResetAll(): Promise<void> {
    confirmResetAll.value = false
    resettingAll.value = true
    resetFeedback.value = null
    resetError.value = null
    try {
        const result = await resetAllGoogleBindings()
        await reloadBindings()
        resetFeedback.value = `Reset Google access for ${result.reset} user${result.reset === 1 ? '' : 's'}; skipped ${result.skipped} without a password.`
    } catch (e: unknown) {
        resetError.value = e instanceof Error ? e.message : 'Failed to reset all bindings'
    } finally {
        resettingAll.value = false
    }
}
</script>

<style scoped>
.admin-mcp-view {
    max-width: 700px;
    margin: 0 auto;
    padding: 1.5rem;
}

.status-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.status-label {
    font-size: 0.875rem;
    color: var(--color-foreground);
    min-width: 9rem;
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

.disabled-reason {
    margin-top: 0.5rem;
    font-size: 0.8125rem;
    color: var(--color-muted-foreground);
}

.detail-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.375rem 0;
    border-bottom: 1px solid var(--color-border);
}

.detail-row:last-child {
    border-bottom: none;
}

.detail-label {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    min-width: 9rem;
    flex-shrink: 0;
    padding-top: 0.125rem;
}

.detail-value {
    font-size: 0.875rem;
    color: var(--color-foreground);
    font-weight: 500;
}

.detail-value--mono {
    font-family: monospace;
    font-weight: 400;
    word-break: break-all;
}

.endpoint-row {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    flex: 1;
    min-width: 0;
}

.endpoint-row code {
    flex: 1;
    min-width: 0;
}

.hint-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin-bottom: 0.75rem;
}

.hint-url {
    display: block;
    font-family: monospace;
    font-size: 0.8125rem;
    background: var(--color-muted);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    color: var(--color-foreground);
    word-break: break-all;
}

.bindings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
}

.bindings-header h2 {
    margin-bottom: 0;
}

.bindings-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
}

.bindings-table th {
    text-align: left;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-muted-foreground);
    padding: 0.25rem 0.5rem 0.5rem 0;
    border-bottom: 1px solid var(--color-border);
}

.bindings-table td {
    padding: 0.5rem 0.5rem 0.5rem 0;
    border-bottom: 1px solid var(--color-border);
    color: var(--color-foreground);
    vertical-align: middle;
}

.bindings-table tr:last-child td {
    border-bottom: none;
}

.binding-email {
    display: block;
    font-weight: 500;
}

.binding-name {
    display: block;
    font-size: 0.8125rem;
    color: var(--color-muted-foreground);
}

.binding-google-email,
.binding-date {
    color: var(--color-muted-foreground);
    white-space: nowrap;
}

.binding-actions {
    text-align: right;
}

.empty-state {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.binding-feedback {
    margin-top: 0.75rem;
    font-size: 0.875rem;
    color: var(--color-success);
}

.binding-error {
    margin-top: 0.75rem;
    font-size: 0.875rem;
    color: var(--color-destructive);
}

.action-btn {
    background: none;
    border: 1px solid var(--color-border);
    border-radius: 4px;
    padding: 0.125rem 0.5rem;
    font-size: 0.8rem;
    cursor: pointer;
    color: var(--color-foreground);
}

.action-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}

.action-btn--destructive {
    border-color: var(--color-destructive);
    color: var(--color-destructive);
}

.action-btn--destructive:hover {
    background: hsl(from var(--color-destructive) h s l / 0.08);
}
</style>
