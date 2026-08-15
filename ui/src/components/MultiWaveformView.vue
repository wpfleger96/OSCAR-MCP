<template>
    <div class="flex flex-col gap-4">
        <div
            v-for="(chart, idx) in charts"
            :key="chart.id"
            class="border border-border rounded-lg p-3 bg-card"
        >
            <div class="flex items-center justify-between mb-2 max-md:flex-wrap">
                <div class="flex items-center gap-2">
                    <Select
                        :model-value="chart.type"
                        @update:model-value="(v) => updateChartType(idx, v as string)"
                    >
                        <SelectTrigger class="w-[180px] h-8 text-sm">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem
                                v-for="opt in typeOptions"
                                :key="opt.value"
                                :value="opt.value"
                            >
                                {{ opt.label }}
                            </SelectItem>
                        </SelectContent>
                    </Select>
                    <InfoHint :glossary-key="WAVEFORM_GLOSSARY_MAP[chart.type as WaveformType]" />
                </div>
                <Button
                    v-if="charts.length > 1"
                    variant="ghost"
                    size="icon"
                    class="chart-remove text-destructive hover:text-destructive"
                    @click="removeChart(idx)"
                >
                    <X class="h-4 w-4" />
                </Button>
            </div>
            <!-- Full-height spinner only before first data; refetches keep the chart mounted with a corner spinner (:refetching) -->
            <div
                v-if="chart.loading && !chart.data"
                class="h-60 flex items-center justify-center gap-2 text-muted-foreground"
            >
                <Loader2 class="h-4 w-4 animate-spin" />
                Loading {{ WAVEFORM_LABELS[chart.type] ?? chart.type }}...
            </div>
            <div
                v-else-if="chart.error && !chart.data"
                class="h-60 flex items-center justify-center gap-2 text-destructive"
            >
                <AlertTriangle class="h-4 w-4" />
                {{ chart.error }}
            </div>
            <WaveformChart
                v-else-if="chart.data"
                :ref="(el) => setChartRef(idx, el)"
                :timestamps="chart.data.timestamps"
                :values="chart.data.values"
                :unit="chart.data.unit"
                :label="WAVEFORM_LABELS[chart.type] ?? chart.type"
                :waveform-type="chart.type"
                :events="chart.type === 'flow' ? events : undefined"
                :sync-key="syncKey"
                :start-epoch="startEpoch"
                :refetching="chart.loading"
                @zoom="onZoom"
            />
            <div
                v-if="chart.data && chart.error && !chart.loading"
                class="mt-1 text-sm text-destructive"
            >
                Failed to refresh waveform: {{ chart.error }}
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import uPlot from 'uplot'
import { X, Loader2, AlertTriangle } from '@lucide/vue'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import WaveformChart from './WaveformChart.vue'
import InfoHint from '@/components/InfoHint.vue'
import { getWaveformData } from '@/api/waveforms'
import { WAVEFORM_LABELS, WAVEFORM_GLOSSARY_MAP } from '@/types'
import type { WaveformDataResponse, EventItem, WaveformType } from '@/types'

const props = defineProps<{
    sessionId: number
    availableTypes: string[]
    events?: EventItem[]
    initialTypes?: string[]
    startEpoch: number
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
const chartAborts = new Map<number, AbortController>()

const typeOptions = computed(() =>
    props.availableTypes.map((t) => ({ value: t, label: WAVEFORM_LABELS[t] ?? t })),
)

function setChartRef(idx: number, el: unknown): void {
    chartRefs.value[idx] = el as InstanceType<typeof WaveformChart> | null
}

async function loadChart(chart: ChartState, startSec?: number, endSec?: number): Promise<void> {
    chartAborts.get(chart.id)?.abort()
    const thisController = new AbortController()
    chartAborts.set(chart.id, thisController)

    chart.loading = true
    chart.error = null
    try {
        const params: Record<string, number> = { max_points: 2000 }
        if (startSec !== undefined) params.start_seconds = startSec
        if (endSec !== undefined) params.end_seconds = endSec
        chart.data = await getWaveformData(
            props.sessionId,
            chart.type,
            params,
            thisController.signal,
        )
    } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'CanceledError') {
            chart.error = err.message
        }
    } finally {
        if (chartAborts.get(chart.id) === thisController) {
            chart.loading = false
        }
    }
}

function addChart(type: string): void {
    if (charts.value.length >= 4) return
    charts.value.push({ id: nextId++, type, data: null, loading: false, error: null })
    void loadChart(charts.value[charts.value.length - 1]!)
}

function removeChart(idx: number): void {
    const chart = charts.value[idx]
    if (chart) {
        chartAborts.get(chart.id)?.abort()
        chartAborts.delete(chart.id)
    }
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
    zoomTo(startSec: number, endSec: number) {
        for (const chart of charts.value) {
            void loadChart(chart, startSec, endSec)
        }
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
@media (max-width: 767.98px) {
    /* shadcn size="icon" is layered (36px) and loses to scoped CSS, so the touch floor lives here */
    .chart-remove {
        min-height: var(--tap-target);
        min-width: var(--tap-target);
    }
}
</style>
