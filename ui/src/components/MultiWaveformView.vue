<template>
    <div class="flex flex-col gap-4">
        <div
            v-for="(chart, idx) in charts"
            :key="chart.id"
            class="border border-border rounded-lg p-3 bg-card"
        >
            <div class="flex items-center justify-between mb-2">
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
                    <InfoHint
                        v-if="WAVEFORM_GLOSSARY_MAP[chart.type as WaveformType]"
                        :glossary-key="WAVEFORM_GLOSSARY_MAP[chart.type as WaveformType]!"
                    />
                </div>
                <Button
                    v-if="charts.length > 1"
                    variant="ghost"
                    size="icon"
                    class="text-destructive hover:text-destructive"
                    @click="removeChart(idx)"
                >
                    <X class="h-4 w-4" />
                </Button>
            </div>
            <div
                v-if="chart.loading"
                class="h-60 flex items-center justify-center gap-2 text-muted-foreground"
            >
                <Loader2 class="h-4 w-4 animate-spin" />
                Loading {{ WAVEFORM_LABELS[chart.type] ?? chart.type }}...
            </div>
            <div
                v-else-if="chart.error"
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
                @zoom="onZoom"
            />
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
