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
import { Loader2, AlertTriangle } from '@lucide/vue'
import { useApiLoad } from '@/composables/useApiLoad'
import { getAbout } from '@/api/about'

const { data, loading, error } = useApiLoad(() => getAbout())

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
</style>
