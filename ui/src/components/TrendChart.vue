<template>
    <div ref="containerRef" class="trend-chart" />
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { useDarkMode } from '@/composables/useDarkMode'

const { isDark } = useDarkMode()

const props = defineProps<{
    labels: string[]
    datasets: { label: string; values: (number | null)[]; color: string }[]
    height?: number
    syncKey?: uPlot.SyncPubSub
}>()

const containerRef = ref<HTMLDivElement>()
let chart: uPlot | null = null

function axisColors() {
    return isDark.value ? { axis: '#a1a1aa', grid: '#27272a' } : { axis: '#888', grid: '#eee' }
}

function createChart(): void {
    if (!containerRef.value || !props.labels.length) return

    chart?.destroy()

    const colors = axisColors()
    const timestamps = props.labels.map((d) => new Date(d).getTime() / 1000)
    const width = containerRef.value.clientWidth || 800

    const opts: uPlot.Options = {
        width,
        height: props.height ?? 280,
        cursor: {
            sync: props.syncKey ? { key: props.syncKey.key } : undefined,
            drag: { x: true, y: false, setScale: true },
        },
        scales: { x: { time: true } },
        axes: [
            {
                space: 80,
                stroke: colors.axis,
                grid: { stroke: colors.grid },
                ticks: { stroke: colors.axis },
            },
            {
                size: 60,
                stroke: colors.axis,
                grid: { stroke: colors.grid },
                ticks: { stroke: colors.axis },
            },
        ],
        series: [
            {},
            ...props.datasets.map((ds) => ({
                label: ds.label,
                stroke: ds.color,
                width: 2,
                spanGaps: true,
                points: { size: 4 },
            })),
        ],
    }

    const data: uPlot.AlignedData = [
        timestamps,
        ...props.datasets.map(
            (ds) => ds.values.map((v) => v ?? undefined) as (number | undefined)[],
        ),
    ]

    chart = new uPlot(opts, data, containerRef.value)
}

function handleResize(): void {
    if (!chart || !containerRef.value) return
    chart.setSize({ width: containerRef.value.clientWidth, height: chart.height })
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
    createChart()
    resizeObserver = new ResizeObserver(handleResize)
    if (containerRef.value) resizeObserver.observe(containerRef.value)
})

onBeforeUnmount(() => {
    chart?.destroy()
    chart = null
    resizeObserver?.disconnect()
})

watch(
    () => [props.labels, props.datasets, props.height, props.syncKey] as const,
    () => createChart(),
    { deep: true },
)

watch(isDark, () => createChart())
</script>

<style scoped>
.trend-chart {
    width: 100%;
}
</style>
