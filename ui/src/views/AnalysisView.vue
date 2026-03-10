<template>
    <div v-if="loading" class="loading-state">
        <i class="pi pi-spin pi-spinner" /> Loading analysis...
    </div>

    <div v-else-if="noAnalysis" class="no-analysis">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <i class="pi pi-arrow-left" /> Back to Session
        </RouterLink>
        <div class="empty-card">
            <i class="pi pi-chart-bar empty-icon" />
            <p>No analysis results for this session.</p>
            <Button
                label="Run Analysis"
                icon="pi pi-play"
                :loading="running"
                @click="handleRunAnalysis"
            />
        </div>
    </div>

    <div v-else-if="error" class="error-state">
        <i class="pi pi-exclamation-triangle" /> {{ error }}
    </div>

    <div v-else-if="analysis" class="analysis-view">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <i class="pi pi-arrow-left" /> Back to Session
        </RouterLink>

        <h1 class="page-title">Analysis — Session #{{ sessionId }}</h1>

        <!-- Summary -->
        <div class="summary-row">
            <StatCard
                label="Duration"
                :value="analysis.session_duration_hours"
                unit="hrs"
                :decimals="1"
            />
            <StatCard label="Total Breaths" :value="analysis.total_breaths" :decimals="0" />
            <StatCard
                label="Machine Events"
                :value="analysis.machine_events.length"
                :decimals="0"
            />
            <StatCard
                v-if="analysis.pulse_change_count != null"
                label="Pulse Changes"
                :value="analysis.pulse_change_count"
                :decimals="0"
            />
        </div>

        <!-- Mode Comparison Table -->
        <div class="section-card">
            <h2>Mode Comparison</h2>
            <DataTable :value="modeRows" striped-rows>
                <Column field="mode" header="Mode" />
                <Column header="AHI" style="width: 80px">
                    <template #body="{ data }">
                        <strong>{{ data.ahi.toFixed(1) }}</strong>
                    </template>
                </Column>
                <Column header="RDI" style="width: 80px">
                    <template #body="{ data }">{{ data.rdi.toFixed(1) }}</template>
                </Column>
                <Column field="apneas" header="Apneas" style="width: 80px" />
                <Column field="hypopneas" header="Hypopneas" style="width: 100px" />
                <Column field="reras" header="RERAs" style="width: 80px" />
            </DataTable>
        </div>

        <!-- Per-mode Events -->
        <div class="section-card">
            <h2>Events by Mode</h2>
            <SelectButton v-model="selectedMode" :options="modeOptions" class="mode-selector" />

            <DataTable
                v-if="selectedModeResult"
                :value="modeEvents"
                striped-rows
                :rows="25"
                paginator
            >
                <Column header="Type" style="width: 80px">
                    <template #body="{ data }">
                        <span
                            class="event-badge"
                            :style="{ background: EVENT_COLORS[data.type] ?? '#ddd' }"
                        >
                            {{ data.type }}
                        </span>
                    </template>
                </Column>
                <Column header="Start Time">
                    <template #body="{ data }">{{ formatTime(data.start) }}</template>
                </Column>
                <Column header="Duration" style="width: 90px">
                    <template #body="{ data }">{{ data.duration.toFixed(1) }}s</template>
                </Column>
                <Column header="Flow Red." style="width: 100px">
                    <template #body="{ data }"
                        >{{ (data.flowReduction * 100).toFixed(0) }}%</template
                    >
                </Column>
                <Column header="Confidence" style="width: 100px">
                    <template #body="{ data }">{{ (data.confidence * 100).toFixed(0) }}%</template>
                </Column>
            </DataTable>
        </div>

        <!-- CSR / Periodic Breathing -->
        <div v-if="analysis.csr_detection || analysis.periodic_breathing" class="section-card">
            <h2>Breathing Patterns</h2>
            <div v-if="analysis.csr_episodes?.length" class="pattern-info">
                <strong>CSR Episodes:</strong> {{ analysis.csr_episodes.length }}
            </div>
            <div v-if="analysis.periodic_breathing_episodes?.length" class="pattern-info">
                <strong>Periodic Breathing Episodes:</strong>
                {{ analysis.periodic_breathing_episodes.length }}
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import SelectButton from 'primevue/selectbutton'
import StatCard from '@/components/StatCard.vue'
import { getAnalysis, runAnalysis } from '@/api/analysis'
import { EVENT_COLORS } from '@/types'
import type { AnalysisResult } from '@/types'

const props = defineProps<{ sessionId: number }>()

const loading = ref(true)
const error = ref<string | null>(null)
const noAnalysis = ref(false)
const running = ref(false)
const analysis = ref<AnalysisResult | null>(null)
const selectedMode = ref('')

const modeOptions = computed(() => Object.keys(analysis.value?.mode_results ?? {}))

const selectedModeResult = computed(() => {
    if (!analysis.value || !selectedMode.value) return null
    return analysis.value.mode_results[selectedMode.value] ?? null
})

interface ModeRow {
    mode: string
    ahi: number
    rdi: number
    apneas: number
    hypopneas: number
    reras: number
}

const modeRows = computed<ModeRow[]>(() => {
    if (!analysis.value) return []
    return Object.entries(analysis.value.mode_results).map(([name, r]) => ({
        mode: name,
        ahi: r.ahi,
        rdi: r.rdi,
        apneas: r.apneas.length,
        hypopneas: r.hypopneas.length,
        reras: r.reras.length,
    }))
})

interface EventRow {
    type: string
    start: number
    duration: number
    flowReduction: number
    confidence: number
}

const modeEvents = computed<EventRow[]>(() => {
    const r = selectedModeResult.value
    if (!r) return []
    const events: EventRow[] = []
    for (const a of r.apneas) {
        events.push({
            type: a.event_type,
            start: a.start_time,
            duration: a.duration,
            flowReduction: a.flow_reduction,
            confidence: a.confidence,
        })
    }
    for (const h of r.hypopneas) {
        events.push({
            type: 'H',
            start: h.start_time,
            duration: h.duration,
            flowReduction: h.flow_reduction,
            confidence: h.confidence,
        })
    }
    for (const re of r.reras) {
        events.push({
            type: 'RE',
            start: re.start_time,
            duration: re.duration,
            flowReduction: 0,
            confidence: re.confidence,
        })
    }
    events.sort((a, b) => a.start - b.start)
    return events
})

function formatTime(secs: number): string {
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    const s = Math.floor(secs % 60)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

async function handleRunAnalysis(): Promise<void> {
    running.value = true
    try {
        analysis.value = await runAnalysis(props.sessionId)
        noAnalysis.value = false
        if (modeOptions.value.length > 0) {
            selectedMode.value = modeOptions.value[0]
        }
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to run analysis'
    } finally {
        running.value = false
    }
}

onMounted(async () => {
    try {
        analysis.value = await getAnalysis(props.sessionId)
        if (modeOptions.value.length > 0) {
            selectedMode.value = modeOptions.value[0]
        }
    } catch (err: unknown) {
        const status = (err as { response?: { status?: number } }).response?.status
        if (status === 404) {
            noAnalysis.value = true
        } else {
            error.value = err instanceof Error ? err.message : 'Failed to load analysis'
        }
    } finally {
        loading.value = false
    }
})
</script>

<style scoped>
.analysis-view,
.no-analysis {
    max-width: 1200px;
}

.summary-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1.25rem;
}

.mode-selector {
    margin-bottom: 0.75rem;
}

.empty-card {
    text-align: center;
    padding: 3rem;
    background: var(--p-surface-card, #fff);
    border: 1px solid var(--p-surface-border, #e2e8f0);
    border-radius: 8px;
}

.empty-icon {
    font-size: 2.5rem;
    color: var(--p-text-muted-color, #6b7280);
    margin-bottom: 1rem;
}

.empty-card p {
    margin-bottom: 1rem;
    color: var(--p-text-muted-color, #6b7280);
}

.event-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
}

.pattern-info {
    font-size: 0.9rem;
    padding: 0.4rem 0;
}
</style>
