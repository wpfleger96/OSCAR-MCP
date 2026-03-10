<template>
    <div class="rx-history">
        <h1 class="page-title">RX History</h1>

        <div v-if="loading" class="loading-state">
            <i class="pi pi-spin pi-spinner" /> Loading RX data...
        </div>

        <div v-else-if="!history.length" class="no-data">
            <i class="pi pi-info-circle" /> No prescription data available.
        </div>

        <div v-else-if="error" class="error-state">
            <i class="pi pi-exclamation-triangle" /> {{ error }}
        </div>

        <template v-else>
            <!-- Current Settings -->
            <div v-if="current" class="section-card">
                <h2>Current Settings</h2>
                <div class="current-meta">
                    <span
                        >{{ formatDate(current.start_date) }} –
                        {{ formatDate(current.end_date) }}</span
                    >
                    <span>{{ current.days_count }} days</span>
                    <span v-if="current.avg_ahi != null"
                        >Avg AHI: {{ current.avg_ahi.toFixed(1) }}</span
                    >
                    <span v-if="current.avg_hours != null"
                        >Avg {{ current.avg_hours.toFixed(1) }} hrs/night</span
                    >
                </div>
                <div class="settings-pills">
                    <Tag
                        v-for="(value, key) in current.settings"
                        :key="key"
                        :value="`${key}: ${value}`"
                        severity="info"
                        class="setting-pill"
                    />
                </div>
            </div>

            <!-- Comparison Table -->
            <div v-if="comparison" class="section-card">
                <h2>Period Comparison</h2>
                <DataTable :value="comparisonRows" striped-rows :row-class="rowClass">
                    <Column header="Period">
                        <template #body="{ data }">
                            {{ formatDate(data.start_date) }} – {{ formatDate(data.end_date) }}
                        </template>
                    </Column>
                    <Column header="Days" style="width: 70px">
                        <template #body="{ data }">{{ data.days_count }}</template>
                    </Column>
                    <Column header="Settings">
                        <template #body="{ data }">
                            {{ summarizeSettings(data.settings) }}
                        </template>
                    </Column>
                    <Column header="Avg AHI" style="width: 90px">
                        <template #body="{ data }">
                            {{ data.avg_ahi?.toFixed(1) ?? '---' }}
                        </template>
                    </Column>
                    <Column header="Median AHI" style="width: 100px">
                        <template #body="{ data }">
                            {{ data.median_ahi?.toFixed(1) ?? '---' }}
                        </template>
                    </Column>
                    <Column header="Avg Hours" style="width: 90px">
                        <template #body="{ data }">
                            {{ data.avg_hours?.toFixed(1) ?? '---' }}
                        </template>
                    </Column>
                    <Column header="Avg Leak" style="width: 90px">
                        <template #body="{ data }">
                            {{ data.avg_leak?.toFixed(1) ?? '---' }}
                        </template>
                    </Column>
                    <Column header="" style="width: 80px">
                        <template #body="{ data }">
                            <Tag v-if="data.isBest" value="Best" severity="success" />
                            <Tag v-if="data.isWorst" value="Worst" severity="danger" />
                        </template>
                    </Column>
                </DataTable>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import { getRxHistory, getRxCurrent, getRxCompare } from '@/api/rx'
import type { RxPeriodResponse, RxComparisonResponse } from '@/types'

const loading = ref(true)
const error = ref<string | null>(null)
const history = ref<RxPeriodResponse[]>([])
const current = ref<RxPeriodResponse | null>(null)
const comparison = ref<RxComparisonResponse | null>(null)

interface ComparisonRow extends RxPeriodResponse {
    isBest: boolean
    isWorst: boolean
}

const comparisonRows = computed<ComparisonRow[]>(() => {
    if (!comparison.value) return []
    return comparison.value.periods.map((p, i) => ({
        ...p,
        isBest: comparison.value!.best_index === i,
        isWorst: comparison.value!.worst_index === i,
    }))
})

function rowClass(data: ComparisonRow): string {
    if (data.isBest) return 'row-best'
    if (data.isWorst) return 'row-worst'
    return ''
}

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

function summarizeSettings(settings: Record<string, string>): string {
    const keys = ['Mode', 'Pressure', 'EPR', 'EPR Level']
    const parts: string[] = []
    for (const k of keys) {
        if (k in settings) parts.push(`${k}: ${settings[k]}`)
    }
    if (!parts.length) {
        return Object.entries(settings)
            .slice(0, 3)
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
    }
    return parts.join(', ')
}

onMounted(async () => {
    try {
        const [h, c, comp] = await Promise.all([getRxHistory(), getRxCurrent(), getRxCompare()])
        history.value = h
        current.value = c
        comparison.value = comp
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to load RX data'
    } finally {
        loading.value = false
    }
})
</script>

<style scoped>
.rx-history {
    max-width: 1200px;
}

.no-data {
    padding: 2rem;
    text-align: center;
    color: var(--p-text-muted-color, #6b7280);
}

.current-meta {
    display: flex;
    gap: 1.25rem;
    flex-wrap: wrap;
    font-size: 0.9rem;
    color: var(--p-text-muted-color, #6b7280);
    margin-bottom: 0.75rem;
}

.settings-pills {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.setting-pill {
    font-size: 0.78rem;
}

:deep(.row-best) {
    background: rgba(34, 197, 94, 0.08) !important;
}
:deep(.row-worst) {
    background: rgba(239, 68, 68, 0.08) !important;
}
</style>
