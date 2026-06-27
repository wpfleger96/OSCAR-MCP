<template>
    <div v-if="loading" class="loading-state">
        <Loader2 class="inline h-4 w-4 animate-spin" /> Loading session...
    </div>

    <div v-else-if="error" class="error-state">
        <AlertTriangle class="inline h-4 w-4" /> {{ error }}
    </div>

    <div v-else-if="session" class="session-detail">
        <!-- Back link -->
        <RouterLink to="/sessions" class="back-link">
            <ArrowLeft class="inline h-4 w-4" /> All Sessions
        </RouterLink>

        <!-- Session header -->
        <div class="session-header">
            <div>
                <h1>{{ formatDateWithWeekday(session.start_time) }}</h1>
                <div class="session-meta text-muted-foreground">
                    <Badge v-if="session.therapy_mode">{{ session.therapy_mode }}</Badge>
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
        <div class="bg-card border border-border rounded-lg py-4 px-5 mb-6">
            <WaveformToolbar
                v-model="selectedType"
                :available-types="session.waveform_types"
                v-model:multi-waveform="multiMode"
                :chart-count="multiViewRef?.chartCount ?? 1"
                @reset-zoom="handleResetZoom"
                @add-chart="handleAddChart"
            />

            <div
                v-if="waveformLoading && !multiMode"
                class="h-60 flex items-center justify-center gap-2 text-muted-foreground"
            >
                <Loader2 class="h-4 w-4 animate-spin" /> Loading waveform...
            </div>
            <div
                v-else-if="waveformError && !multiMode"
                class="h-60 flex items-center justify-center gap-2 text-destructive"
            >
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
        <Collapsible
            v-if="session.settings?.length"
            v-model:open="settingsOpen"
            class="settings-panel"
        >
            <CollapsibleTrigger as-child>
                <button
                    class="flex w-full items-center justify-between rounded-lg border border-border bg-card p-4 text-left font-semibold hover:bg-accent"
                >
                    Device Settings
                    <ChevronDown
                        class="h-4 w-4 transition-transform"
                        :class="{ 'rotate-180': settingsOpen }"
                    />
                </button>
            </CollapsibleTrigger>
            <CollapsibleContent class="px-4 pt-3 pb-4">
                <div class="settings-grid">
                    <div v-for="s in session.settings" :key="s.key" class="setting-row">
                        <span class="setting-key text-muted-foreground">{{ s.key }}</span>
                        <span class="setting-value">{{ s.value ?? '---' }}</span>
                    </div>
                </div>
            </CollapsibleContent>
        </Collapsible>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Loader2, AlertTriangle, ArrowLeft, ChevronDown } from '@lucide/vue'
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
const settingsOpen = ref(false)

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
    border-bottom: 1px solid var(--color-border);
}
</style>
