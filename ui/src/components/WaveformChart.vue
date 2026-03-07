<template>
    <div ref="containerRef" class="waveform-chart" />
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { EVENT_COLORS } from '@/types'
import type { EventItem } from '@/types'

const props = defineProps<{
    timestamps: number[]
    values: number[]
    unit: string
    label: string
    events?: EventItem[]
    syncKey?: uPlot.SyncPubSub
}>()

const emit = defineEmits<{
    zoom: [startSec: number, endSec: number]
}>()

const containerRef = ref<HTMLDivElement>()
let chart: uPlot | null = null
let debounceTimer: ReturnType<typeof setTimeout> | null = null
let isInitialRender = true

function buildEventPlugin(): uPlot.Plugin {
    return {
        hooks: {
            drawClear: [
                (u: uPlot) => {
                    if (!props.events?.length) return
                    const ctx = u.ctx
                    const { left, top, width, height } = u.bbox

                    ctx.save()
                    for (const evt of props.events) {
                        const color = EVENT_COLORS[evt.event_type]
                        if (!color) continue

                        const x0 = u.valToPos(evt.offset_seconds, 'x', true)
                        const x1 = u.valToPos(evt.offset_seconds + evt.duration_seconds, 'x', true)

                        if (x1 < left || x0 > left + width) continue

                        const cx0 = Math.max(x0, left)
                        const cx1 = Math.min(x1, left + width)

                        ctx.fillStyle = color
                        ctx.fillRect(cx0, top, cx1 - cx0, height)
                    }
                    ctx.restore()
                },
            ],
        },
    }
}

function formatTime(secs: number): string {
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    return `${h}:${String(m).padStart(2, '0')}`
}

function createChart(): void {
    if (!containerRef.value || !props.timestamps.length) return

    chart?.destroy()
    isInitialRender = true

    const width = containerRef.value.clientWidth || 800
    const height = 240

    const opts: uPlot.Options = {
        width,
        height,
        plugins: [buildEventPlugin()],
        cursor: {
            sync: props.syncKey ? { key: props.syncKey.key } : undefined,
            drag: { x: true, y: false, setScale: true },
        },
        scales: {
            x: { time: false },
        },
        axes: [
            {
                // x-axis: show H:MM labels
                values: (_u: uPlot, vals: number[]) => vals.map(formatTime),
                space: 80,
            },
            {
                label: `${props.label} (${props.unit})`,
                size: 70,
            },
        ],
        series: [
            {},
            {
                label: props.label,
                stroke: '#2563eb',
                width: 1,
                fill: 'rgba(37, 99, 235, 0.05)',
            },
        ],
        hooks: {
            setScale: [
                (u: uPlot, scaleKey: string) => {
                    if (scaleKey !== 'x') return
                    // Skip the initial render scale-set
                    if (isInitialRender) {
                        isInitialRender = false
                        return
                    }
                    const min = u.scales.x.min
                    const max = u.scales.x.max
                    if (min == null || max == null) return

                    if (debounceTimer) clearTimeout(debounceTimer)
                    debounceTimer = setTimeout(() => {
                        emit('zoom', min, max)
                    }, 300)
                },
            ],
        },
    }

    const data: uPlot.AlignedData = [props.timestamps, props.values]
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
    if (debounceTimer) clearTimeout(debounceTimer)
})

// Update data in-place when timestamps/values change (avoids canvas flicker)
watch(
    () => [props.timestamps, props.values] as const,
    async ([ts, vals]) => {
        if (!ts.length) return
        if (!chart) {
            await nextTick()
            createChart()
            return
        }
        isInitialRender = true
        chart.setData([ts, vals])
    },
)

// Redraw when events change (overlay update)
watch(
    () => props.events,
    () => chart?.redraw(),
    { deep: false },
)

defineExpose({
    resetZoom() {
        if (!chart || !props.timestamps.length) return
        isInitialRender = true
        chart.setScale('x', {
            min: props.timestamps[0],
            max: props.timestamps[props.timestamps.length - 1],
        })
    },
})
</script>

<style scoped>
.waveform-chart {
    width: 100%;
}

.waveform-chart :deep(.uplot) {
    width: 100% !important;
}
</style>
