<template>
    <div v-if="loading" class="loading-state">
        <i class="pi pi-spin pi-spinner" /> Loading session...
    </div>

    <div v-else-if="error" class="error-state">
        <i class="pi pi-exclamation-triangle" /> {{ error }}
    </div>

    <div v-else-if="session" class="session-detail">
        <!-- Back link -->
        <RouterLink to="/sessions" class="back-link">
            <i class="pi pi-arrow-left" /> All Sessions
        </RouterLink>

        <!-- Session header -->
        <div class="session-header">
            <div>
                <h1>{{ formatDateWithWeekday(session.start_time) }}</h1>
                <div class="session-meta">
                    <Tag v-if="session.therapy_mode" :value="session.therapy_mode" />
                    <span>{{ session.device_manufacturer }} {{ session.device_model }}</span>
                    <span>{{ session.duration_hours.toFixed(1) }} hours</span>
                    <span v-if="session.statistics?.ahi != null">
                        AHI:
                        <strong :class="ahiClass(session.statistics.ahi)">{{
                            session.statistics.ahi.toFixed(1)
                        }}</strong>
                    </span>
                    <span>{{ session.event_count }} events</span>
                </div>
            </div>
        </div>

        <!-- Waveform section -->
        <div class="waveform-section">
            <WaveformToolbar
                v-model="selectedType"
                :available-types="session.waveform_types"
                v-model:multi-waveform="multiMode"
                :chart-count="multiViewRef?.chartCount ?? 1"
                @reset-zoom="handleResetZoom"
                @add-chart="handleAddChart"
            />

            <div v-if="waveformLoading && !multiMode" class="chart-loading">
                <i class="pi pi-spin pi-spinner" /> Loading waveform...
            </div>
            <div v-else-if="waveformError && !multiMode" class="chart-error">
                {{ waveformError }}
            </div>

            <template v-if="!multiMode">
                <WaveformChart
                    v-if="waveformData"
                    ref="singleChartRef"
                    :timestamps="waveformData.timestamps"
                    :values="waveformData.values"
                    :unit="waveformData.unit"
                    :label="selectedType"
                    :events="selectedType === 'flow' ? events : undefined"
                    @zoom="handleZoom"
                />
            </template>

            <MultiWaveformView
                v-else
                ref="multiViewRef"
                :session-id="session.id"
                :available-types="session.waveform_types"
                :events="events"
                :initial-types="[selectedType]"
                @zoom="handleZoom"
            />
        </div>

        <!-- Statistics grid -->
        <div v-if="session.statistics" class="stats-section">
            <h2>Statistics</h2>
            <div class="stats-grid">
                <StatCard label="AHI" :value="session.statistics.ahi" :decimals="1" />
                <StatCard
                    label="Usage"
                    :value="session.statistics.usage_hours"
                    unit="hrs"
                    :decimals="1"
                />
                <StatCard
                    label="Pressure 95th"
                    :value="session.statistics.pressure_95th"
                    unit="cmH₂O"
                    :decimals="1"
                />
                <StatCard
                    label="Leak 95th"
                    :value="session.statistics.leak_95th"
                    unit="L/min"
                    :decimals="1"
                />
                <StatCard
                    label="SpO₂ Mean"
                    :value="session.statistics.spo2_mean"
                    unit="%"
                    :decimals="1"
                />
                <StatCard
                    label="Pulse Mean"
                    :value="session.statistics.pulse_mean"
                    unit="bpm"
                    :decimals="0"
                />
            </div>
        </div>

        <!-- Device settings -->
        <Panel
            v-if="session.settings?.length"
            header="Device Settings"
            :toggleable="true"
            :collapsed="true"
            class="settings-panel"
        >
            <div class="settings-grid">
                <div v-for="s in session.settings" :key="s.key" class="setting-row">
                    <span class="setting-key">{{ s.key }}</span>
                    <span class="setting-value">{{ s.value ?? '---' }}</span>
                </div>
            </div>
        </Panel>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import Tag from 'primevue/tag'
import Panel from 'primevue/panel'
import WaveformChart from '@/components/WaveformChart.vue'
import WaveformToolbar from '@/components/WaveformToolbar.vue'
import MultiWaveformView from '@/components/MultiWaveformView.vue'
import StatCard from '@/components/StatCard.vue'
import { getSession } from '@/api/sessions'
import { getSessionEvents } from '@/api/events'
import { useWaveformData } from '@/composables/useWaveformData'
import { ahiClass } from '@/utils/format'
import { formatDateWithWeekday } from '@/utils/formatting'
import type { SessionDetail, EventItem } from '@/types'

const props = defineProps<{ sessionId: number }>()
const route = useRoute()

const session = ref<SessionDetail | null>(null)
const events = ref<EventItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const selectedType = ref('')
const multiMode = ref(false)

const sessionIdRef = computed(() => props.sessionId)
const {
    data: waveformData,
    loading: waveformLoading,
    error: waveformError,
    loadData,
} = useWaveformData(sessionIdRef, selectedType)

const singleChartRef = ref<InstanceType<typeof WaveformChart>>()
const multiViewRef = ref<InstanceType<typeof MultiWaveformView>>()

const jumpToTime = route.query.t ? Number(route.query.t) : null

// Jump to timestamp from ?t= query param after first waveform load
if (jumpToTime != null) {
    const stopWatch = watch(waveformData, (data) => {
        if (data) {
            stopWatch()
            nextTick(() => {
                const padding = 300
                singleChartRef.value?.setScaleX(
                    Math.max(0, jumpToTime - padding),
                    jumpToTime + padding,
                )
            })
        }
    })
}

async function handleZoom(startSec: number, endSec: number): Promise<void> {
    if (!multiMode.value) {
        await loadData(startSec, endSec)
    }
}

function handleResetZoom(): void {
    if (multiMode.value) {
        multiViewRef.value?.resetZoom()
    } else {
        void loadData()
        singleChartRef.value?.resetZoom()
    }
}

function handleAddChart(): void {
    if (!session.value || !multiViewRef.value) return
    const usedTypes = multiViewRef.value.chartTypes()
    const next = session.value.waveform_types.find((t) => !usedTypes.includes(t))
    if (next) multiViewRef.value.addChart(next)
}

watch(selectedType, (newType) => {
    if (!multiMode.value && newType) void loadData()
})

onMounted(async () => {
    try {
        session.value = await getSession(props.sessionId)

        selectedType.value = session.value.waveform_types.includes('flow')
            ? 'flow'
            : (session.value.waveform_types[0] ?? '')
        // watcher triggers loadData() for the selected type

        if (session.value.has_event_data) {
            events.value = await getSessionEvents(props.sessionId)
        }
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to load session'
    } finally {
        loading.value = false
    }
})
</script>

<style scoped>
.session-detail {
    max-width: 1200px;
}

.session-header {
    margin-bottom: 1.5rem;
}

.session-header h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}

.session-meta {
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
    font-size: 0.9rem;
    color: var(--p-text-muted-color, #6b7280);
}

.waveform-section {
    background: var(--p-surface-card, #fff);
    border: 1px solid var(--p-surface-border, #e2e8f0);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-bottom: 1.5rem;
}

.chart-loading,
.chart-error {
    height: 240px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    color: var(--p-text-muted-color, #6b7280);
}

.chart-error {
    color: var(--p-red-500, #ef4444);
}

.stats-section {
    margin-bottom: 1.5rem;
}

.stats-section h2 {
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
}

.settings-panel {
    margin-bottom: 1.5rem;
}

.settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.4rem 2rem;
}

.setting-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.875rem;
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--p-surface-border, #e2e8f0);
}

.setting-key {
    color: var(--p-text-muted-color, #6b7280);
}
</style>
