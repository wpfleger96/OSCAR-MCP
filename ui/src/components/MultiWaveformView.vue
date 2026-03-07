<template>
    <div class="multi-waveform">
        <div v-for="(chart, idx) in charts" :key="chart.id" class="chart-row">
            <div class="chart-row-header">
                <Select
                    :model-value="chart.type"
                    :options="typeOptions"
                    option-label="label"
                    option-value="value"
                    size="small"
                    @update:model-value="(v: string) => updateChartType(idx, v)"
                />
                <Button
                    v-if="charts.length > 1"
                    icon="pi pi-times"
                    size="small"
                    severity="danger"
                    text
                    rounded
                    @click="removeChart(idx)"
                />
            </div>
            <div v-if="chart.loading" class="chart-placeholder">
                <i class="pi pi-spin pi-spinner" /> Loading
                {{ WAVEFORM_LABELS[chart.type] ?? chart.type }}...
            </div>
            <div v-else-if="chart.error" class="chart-error">
                <i class="pi pi-exclamation-triangle" /> {{ chart.error }}
            </div>
            <WaveformChart
                v-else-if="chart.data"
                :ref="(el) => setChartRef(idx, el)"
                :timestamps="chart.data.timestamps"
                :values="chart.data.values"
                :unit="chart.data.unit"
                :label="WAVEFORM_LABELS[chart.type] ?? chart.type"
                :events="chart.type === 'flow' ? events : undefined"
                :sync-key="syncKey"
                @zoom="onZoom"
            />
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import uPlot from 'uplot'
import Select from 'primevue/select'
import Button from 'primevue/button'
import WaveformChart from './WaveformChart.vue'
import { getWaveformData } from '@/api/waveforms'
import { WAVEFORM_LABELS } from '@/types'
import type { WaveformDataResponse, EventItem } from '@/types'

const props = defineProps<{
    sessionId: number
    availableTypes: string[]
    events?: EventItem[]
    initialTypes?: string[]
}>()

const emit = defineEmits<{
    zoom: [startSec: number, endSec: number]
}>()

interface ChartState {
    id: number
    type: string
    data: WaveformDataResponse | null
    loading: boolean
    error: string | null
}

let nextId = 0
const syncKey = uPlot.sync(`waveform-sync-${crypto.randomUUID()}`)
const charts = ref<ChartState[]>([])
const chartRefs = ref<(InstanceType<typeof WaveformChart> | null)[]>([])

const typeOptions = computed(() =>
    props.availableTypes.map((t) => ({ value: t, label: WAVEFORM_LABELS[t] ?? t })),
)

function setChartRef(idx: number, el: unknown): void {
    chartRefs.value[idx] = el as InstanceType<typeof WaveformChart> | null
}

async function loadChart(chart: ChartState, startSec?: number, endSec?: number): Promise<void> {
    chart.loading = true
    chart.error = null
    try {
        const params: Record<string, number> = { max_points: 2000 }
        if (startSec !== undefined) params.start_seconds = startSec
        if (endSec !== undefined) params.end_seconds = endSec
        chart.data = await getWaveformData(props.sessionId, chart.type, params)
    } catch (err: unknown) {
        chart.error = err instanceof Error ? err.message : 'Failed to load waveform'
    } finally {
        chart.loading = false
    }
}

function addChart(type: string): void {
    if (charts.value.length >= 4) return
    const chart: ChartState = { id: nextId++, type, data: null, loading: false, error: null }
    charts.value.push(chart)
    void loadChart(chart)
}

function removeChart(idx: number): void {
    charts.value.splice(idx, 1)
    chartRefs.value.splice(idx, 1)
}

function updateChartType(idx: number, newType: string): void {
    const chart = charts.value[idx]
    if (!chart) return
    chart.type = newType
    chart.data = null
    chart.error = null
    void loadChart(chart)
}

function onZoom(startSec: number, endSec: number): void {
    for (const chart of charts.value) {
        void loadChart(chart, startSec, endSec)
    }
    emit('zoom', startSec, endSec)
}

onMounted(() => {
    const types = props.initialTypes ?? [props.availableTypes[0] ?? 'flow']
    for (const t of types) addChart(t)
})

defineExpose({
    addChart,
    resetZoom() {
        for (const chart of charts.value) {
            void loadChart(chart)
        }
        chartRefs.value.forEach((r) => r?.resetZoom())
    },
    get chartCount() {
        return charts.value.length
    },
    chartTypes(): string[] {
        return charts.value.map((c) => c.type)
    },
})
</script>

<style scoped>
.multi-waveform {
    display: flex;
    flex-direction: column;
    gap: 1rem;
}

.chart-row {
    border: 1px solid var(--p-surface-border, #e2e8f0);
    border-radius: 8px;
    padding: 0.75rem;
    background: var(--p-surface-card, #fff);
}

.chart-row-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
}

.chart-placeholder {
    height: 240px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    color: var(--p-text-muted-color, #6b7280);
}

.chart-error {
    height: 240px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    color: var(--p-red-500, #ef4444);
}
</style>
