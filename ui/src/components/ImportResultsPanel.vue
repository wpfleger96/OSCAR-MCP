<template>
    <div class="import-results-panel">
        <div class="stats-grid">
            <StatCard label="Imported" :value="result.total_imported" />
            <StatCard label="Skipped" :value="result.total_skipped" />
            <StatCard label="Failed" :value="result.total_failed" />
        </div>

        <div v-if="result.warnings && result.warnings.length > 0" class="warnings-box">
            <div class="warnings-header">
                <AlertTriangle class="h-4 w-4" />
                Warnings
            </div>
            <ul class="warnings-list">
                <li v-for="(w, i) in result.warnings" :key="i">{{ w }}</li>
            </ul>
        </div>

        <div v-if="result.sources && result.sources.length > 0" class="sources-results">
            <h3 class="section-heading">Per-source breakdown</h3>
            <div v-for="(sr, i) in result.sources" :key="i" class="source-result-card">
                <div class="source-result-header">
                    <span class="source-parser">{{ sr.source.parser_name }}</span>
                    <span v-if="sr.source.device_serial" class="source-meta">
                        {{ sr.source.device_serial }}
                    </span>
                </div>
                <div class="source-result-counts">
                    <span class="count-item count-imported">{{ sr.imported }} imported</span>
                    <span class="count-item count-skipped">{{ sr.skipped }} skipped</span>
                    <span class="count-item count-failed">{{ sr.failed }} failed</span>
                </div>
                <div v-if="sr.warnings && sr.warnings.length > 0" class="source-warnings">
                    <div v-for="(w, j) in sr.warnings" :key="j" class="source-warning-item">
                        <AlertTriangle class="h-3 w-3" />
                        {{ w }}
                    </div>
                </div>
            </div>
        </div>

        <div class="panel-actions">
            <Button variant="outline" @click="emit('reset')">Import More</Button>
            <Button @click="router.push({ name: 'sessions' })">
                <Check class="mr-2 h-4 w-4" />
                View Sessions
            </Button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { ImportResult } from '@/types'
import { Button } from '@/components/ui/button'
import StatCard from '@/components/StatCard.vue'
import { Check, AlertTriangle } from '@lucide/vue'

defineProps<{ result: ImportResult }>()
const emit = defineEmits<{ reset: [] }>()

const router = useRouter()
</script>

<style scoped>
.stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
}

.warnings-box {
    border: 1px solid color-mix(in srgb, var(--color-warning, #f59e0b) 40%, transparent);
    background: color-mix(in srgb, var(--color-warning, #f59e0b) 10%, transparent);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.warnings-header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--color-destructive);
}

.warnings-list {
    margin: 0;
    padding-left: 1rem;
    font-size: 0.8rem;
    color: var(--color-destructive);
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
}

.sources-results {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.source-result-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    background: var(--color-card);
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
}

.source-result-header {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
}

.source-result-counts {
    display: flex;
    gap: 1rem;
    font-size: 0.8rem;
}

.count-item {
    font-weight: 500;
}

.count-imported {
    color: var(--color-success, #16a34a);
}

.count-skipped {
    color: var(--color-muted-foreground);
}

.count-failed {
    color: var(--color-destructive);
}

.source-warnings {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    margin-top: 0.2rem;
}

.source-warning-item {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.75rem;
    color: var(--color-destructive);
}

.section-heading {
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted-foreground);
}

.source-parser {
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--color-foreground);
}

.source-meta {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
    margin-top: 0.15rem;
}

.panel-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
    padding-top: 0.5rem;
}
</style>
