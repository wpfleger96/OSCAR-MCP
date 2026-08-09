<template>
    <div class="admin-mcp-view">
        <h1 class="page-title">MCP Server</h1>

        <div v-if="statusLoading" class="loading-state">
            <Loader2 class="h-5 w-5 animate-spin" />
            <span>Loading MCP status…</span>
        </div>

        <div v-else-if="statusError" class="error-state">
            <span>{{ statusError }}</span>
        </div>

        <template v-else-if="status">
            <!-- Status card -->
            <div class="section-card">
                <h2>Status</h2>

                <div class="field-row">
                    <span class="field-label">Server</span>
                    <span v-if="status.enabled" class="status-badge status-badge--active"
                        >Enabled</span
                    >
                    <template v-else>
                        <span class="status-badge status-badge--disabled">Disabled</span>
                        <span v-if="status.disabled_reason" class="field-value">{{
                            status.disabled_reason
                        }}</span>
                    </template>
                </div>

                <div class="field-row">
                    <span class="field-label">Transport</span>
                    <span class="field-value">{{ status.transport ?? '—' }}</span>
                </div>

                <div class="field-row">
                    <span class="field-label">Auth provider</span>
                    <span class="field-value">{{ status.auth_provider ?? '—' }}</span>
                </div>
            </div>

            <!-- Endpoint card -->
            <div v-if="status.enabled && status.endpoint_url" class="section-card">
                <h2>Endpoint</h2>
                <p class="card-caption">
                    MCP clients connect to this URL over
                    {{ status.transport ?? 'streamable-http' }}.
                </p>
                <div class="endpoint-url-row">
                    <input
                        ref="endpointUrlInputRef"
                        :value="status.endpoint_url"
                        readonly
                        class="field-input endpoint-url-input"
                    />
                    <Button variant="outline" type="button" @click="copyEndpointUrl">
                        {{ copied ? 'Copied' : 'Copy' }}
                    </Button>
                </div>
            </div>

            <!-- Linked identities card -->
            <div class="section-card">
                <h2>Linked identities</h2>
                <div class="field-row">
                    <span class="field-label">Google-linked users</span>
                    <span class="field-value">{{ status.linked_google_identities }}</span>
                </div>
                <p class="card-caption">
                    Only accounts with a linked Google identity can authenticate to the MCP server.
                </p>
            </div>

            <!-- Connect a client hint -->
            <div class="section-card">
                <h2>Connect a client</h2>
                <p class="card-caption">
                    Point Claude (or any MCP client) at the endpoint URL above. When the client
                    connects, it starts a Google OAuth sign-in — users authenticate with the same
                    Google account linked to their SNORE login.
                </p>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { useApiLoad } from '@/composables/useApiLoad'
import { getMcpStatus } from '@/api/admin'

const {
    data: status,
    loading: statusLoading,
    error: statusError,
} = useApiLoad(getMcpStatus, 'Failed to load MCP status')

const endpointUrlInputRef = ref<HTMLInputElement | null>(null)
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined

async function copyEndpointUrl(): Promise<void> {
    if (!status.value?.endpoint_url) return
    try {
        await navigator.clipboard.writeText(status.value.endpoint_url)
        copied.value = true
        clearTimeout(copiedTimer)
        copiedTimer = setTimeout(() => (copied.value = false), 2000)
    } catch {
        endpointUrlInputRef.value?.select()
    }
}
</script>

<style scoped>
.admin-mcp-view {
    max-width: 560px;
    margin: 0 auto;
    padding: 1.5rem;
}

.field-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
}

.field-row:last-child {
    margin-bottom: 0;
}

/* min-width aligns labels in their flex rows */
.field-label {
    min-width: 9.5rem;
}

.field-value {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
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

.card-caption {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin-bottom: 0.75rem;
}

.card-caption:last-child {
    margin-bottom: 0;
}

/* Endpoint URL display */
.endpoint-url-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
}

.endpoint-url-input {
    flex: 1;
    font-family: monospace;
    font-size: 0.8125rem;
    min-width: 0;
}
</style>
