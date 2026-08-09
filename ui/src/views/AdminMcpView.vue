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
                <h2>Google Identities</h2>
                <div class="status-row">
                    <span class="status-label">Linked accounts</span>
                    <span class="detail-value">{{ data.linked_google_identities }}</span>
                </div>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Loader2 } from '@lucide/vue'
import { useApiLoad } from '@/composables/useApiLoad'
import { getMcpStatus } from '@/api/admin'

const { data, loading, error } = useApiLoad(getMcpStatus, 'Failed to load MCP status')

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
</style>
