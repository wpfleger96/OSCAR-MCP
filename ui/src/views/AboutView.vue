<template>
    <div class="about-view">
        <h1 class="page-title">About</h1>

        <div v-if="loading && !data" class="loading-row">
            <Loader2 class="h-5 w-5 animate-spin text-muted-foreground" />
            <span class="loading-text">Loading…</span>
        </div>

        <div v-else-if="error" class="error-row">
            <AlertTriangle class="h-4 w-4" />
            {{ error }}
        </div>

        <template v-else-if="data">
            <div v-if="data.update_pending" class="update-banner">
                <Download class="update-banner-icon" />
                <div class="update-banner-body">
                    <span class="update-banner-title">New version waiting to deploy</span>
                    <span class="update-banner-desc">
                        Held while jobs are running<template v-if="data.update_pending_since">
                            (since {{ formatSince(data.update_pending_since) }})</template
                        >. Deploys automatically a few minutes after jobs finish.
                    </span>
                </div>
            </div>

            <section class="section">
                <h2 class="section-heading">Build</h2>
                <div class="overview-card">
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Version</span>
                            <span class="overview-value">v{{ data.version }}</span>
                        </div>
                    </div>
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Commit</span>
                            <span class="overview-value mono">{{ data.git_sha }}</span>
                        </div>
                    </div>
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Built</span>
                            <span class="overview-value">{{ data.build_time || '—' }}</span>
                        </div>
                    </div>
                </div>
            </section>

            <section class="section">
                <h2 class="section-heading">Runtime</h2>
                <div class="overview-card">
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Uptime</span>
                            <span class="overview-value">
                                {{ formatUptime(data.uptime_seconds) }}
                            </span>
                        </div>
                    </div>
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Auth mode</span>
                            <span class="overview-value">{{ data.auth_mode }}</span>
                        </div>
                    </div>
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">Python</span>
                            <span class="overview-value">{{ data.python_version }}</span>
                        </div>
                    </div>
                    <div class="overview-row">
                        <div class="overview-detail">
                            <span class="overview-label">SQLite</span>
                            <span class="overview-value">{{ data.sqlite_version }}</span>
                        </div>
                    </div>
                </div>
            </section>
        </template>
    </div>
</template>

<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { Loader2, AlertTriangle, Download } from '@lucide/vue'
import { getAbout } from '@/api/about'
import type { AboutInfo } from '@/types'

const data = ref<AboutInfo | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let stopped = false
let pollTimer: ReturnType<typeof setTimeout> | null = null

function schedulePoll() {
    if (stopped || pollTimer !== null) return
    pollTimer = setTimeout(async () => {
        pollTimer = null
        if (stopped) return
        await fetchAbout()
    }, 30000)
}

async function fetchAbout() {
    try {
        const result = await getAbout()
        if (stopped) return
        data.value = result
        error.value = null
    } catch (err: unknown) {
        if (data.value === null) {
            error.value = err instanceof Error ? err.message : 'Failed to load info'
        }
        // silently swallow poll errors once data exists
    } finally {
        if (!stopped) {
            loading.value = false
            schedulePoll()
        }
    }
}

void fetchAbout()

onUnmounted(() => {
    stopped = true
    if (pollTimer !== null) {
        clearTimeout(pollTimer)
        pollTimer = null
    }
})

function formatUptime(seconds: number): string {
    const d = Math.floor(seconds / 86400)
    const h = Math.floor((seconds % 86400) / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const parts: string[] = []
    if (d > 0) parts.push(`${d}d`)
    if (h > 0) parts.push(`${h}h`)
    parts.push(`${m}m`)
    return parts.join(' ')
}

function formatSince(iso: string): string {
    try {
        return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    } catch {
        return iso
    }
}
</script>

<style scoped>
.about-view {
    max-width: 1000px;
    margin: 0 auto;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 2rem;
}

.loading-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 2rem 0;
}

.loading-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.error-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.875rem;
    color: var(--color-destructive);
    padding: 1rem 0;
}

.section {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.section-heading {
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted-foreground);
}

.overview-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: var(--color-card);
    padding: 1rem 1.25rem;
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem 2.5rem;
}

.overview-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
}

.overview-detail {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
}

.overview-label {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted-foreground);
}

.overview-value {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--color-foreground);
}

.mono {
    font-family: ui-monospace, monospace;
}

.update-banner {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.875rem 1.25rem;
    border: 1px solid var(--color-warning);
    border-radius: 8px;
    background: color-mix(in srgb, var(--color-warning) 10%, transparent);
}

.update-banner-icon {
    flex-shrink: 0;
    margin-top: 0.125rem;
    color: var(--color-warning);
    width: 1rem;
    height: 1rem;
}

.update-banner-body {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
}

.update-banner-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--color-foreground);
}

.update-banner-desc {
    font-size: 0.825rem;
    color: var(--color-muted-foreground);
}
</style>
