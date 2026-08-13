<template>
    <div v-if="!props.samples.length" class="chart-empty">No sleep stage data to display.</div>
    <template v-else>
        <div ref="containerRef" class="sleep-stage-chart" />
        <div class="legend">
            <span v-for="item in legendItems" :key="item.key" class="legend-item">
                <span class="legend-swatch" :style="{ background: item.color }" />
                {{ item.label }}
            </span>
        </div>
    </template>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { useDarkMode } from '@/composables/useDarkMode'
import type { HealthSampleRead } from '@/types'

const props = defineProps<{
    samples: HealthSampleRead[]
    height?: number
}>()

const { isDark } = useDarkMode()
const containerRef = ref<HTMLDivElement>()
let chart: uPlot | null = null

// Brief spec: bottom→top: InBed(0), Awake(1), AsleepCore(2), AsleepDeep(3), AsleepREM(4)
// AsleepUnspecified shares the Core lane with a muted neutral color.
const STAGE_LANE: Record<string, number> = {
    InBed: 0,
    Awake: 1,
    AsleepCore: 2,
    AsleepUnspecified: 2,
    AsleepDeep: 3,
    AsleepREM: 4,
}

const LANE_LABELS: Record<number, string> = {
    0: 'In Bed',
    1: 'Awake',
    2: 'Core',
    3: 'Deep',
    4: 'REM',
}

// Stage colors — distinct tones in both themes, harmonizing with the rest of the app.
// Saturated mid-range hues pass contrast on both light (white-ish) and dark surfaces.
const COLORS_LIGHT: Record<string, string> = {
    InBed: '#94a3b8', // slate-400
    Awake: '#d97706', // amber-600
    AsleepCore: '#059669', // emerald-600
    AsleepDeep: '#2563eb', // blue-600
    AsleepREM: '#9333ea', // purple-600
    AsleepUnspecified: '#cbd5e1', // slate-200 — muted, distinct from Core
}
const COLORS_DARK: Record<string, string> = {
    InBed: '#475569', // slate-600
    Awake: '#f59e0b', // amber-500
    AsleepCore: '#34d399', // emerald-400
    AsleepDeep: '#60a5fa', // blue-400
    AsleepREM: '#c084fc', // purple-400
    AsleepUnspecified: '#4b5563', // gray-600 — muted, distinct from Core
}

function getColors(): Record<string, string> {
    return isDark.value ? COLORS_DARK : COLORS_LIGHT
}

// Parse a naive local wall-clock ISO datetime without timezone conversion.
// The brief prohibits appending Z or converting timezones — timestamps are
// treated as local wall-clock values for relative positioning only.
function parseLocalDateTime(iso: string): Date {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(iso)
    if (m) {
        return new Date(
            Number(m[1]),
            Number(m[2]) - 1,
            Number(m[3]),
            Number(m[4]),
            Number(m[5]),
            Number(m[6]),
        )
    }
    return new Date(iso)
}

const legendItems = computed(() => {
    const c = getColors()
    return [
        { label: 'In Bed', key: 'InBed', color: c.InBed },
        { label: 'Awake', key: 'Awake', color: c.Awake },
        { label: 'Core (N1+N2)', key: 'AsleepCore', color: c.AsleepCore },
        { label: 'Deep (N3)', key: 'AsleepDeep', color: c.AsleepDeep },
        { label: 'REM', key: 'AsleepREM', color: c.AsleepREM },
        { label: 'Unspecified', key: 'AsleepUnspecified', color: c.AsleepUnspecified },
    ]
})

function createChart(): void {
    if (!containerRef.value || !props.samples.length) return
    chart?.destroy()

    const stageColors = getColors()
    const axisStroke = isDark.value ? '#a1a1aa' : '#888'
    const gridStroke = isDark.value ? '#27272a' : '#eee'

    // Snapshot samples and pre-compute timestamps to avoid re-parsing in the draw hook
    const snapSamples = [...props.samples]
    const startSec = snapSamples.map((s) => parseLocalDateTime(s.start_time).getTime() / 1000)
    const endSec = snapSamples.map((s) => parseLocalDateTime(s.end_time).getTime() / 1000)

    const xMin = Math.min(...startSec)
    const xMax = Math.max(...endSec)

    const drawPlugin: uPlot.Plugin = {
        hooks: {
            draw: (u: uPlot) => {
                const ctx = u.ctx
                ctx.save()
                // Clip rendering to the plot area (excludes axis gutters)
                ctx.beginPath()
                ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height)
                ctx.clip()

                for (let i = 0; i < snapSamples.length; i++) {
                    const vt = snapSamples[i].value_text
                    if (!vt) continue
                    const lane = STAGE_LANE[vt]
                    if (lane === undefined) continue

                    const x0 = u.valToPos(startSec[i], 'x', true)
                    const x1 = u.valToPos(endSec[i], 'x', true)
                    // lane ± 0.4 leaves a 0.1-unit gap between bars (the 0.8 band fills 80% of each lane)
                    // valToPos: higher lane value → smaller canvas y (higher on screen)
                    const yTop = u.valToPos(lane + 0.4, 'y', true)
                    const yBot = u.valToPos(lane - 0.4, 'y', true)

                    ctx.fillStyle = stageColors[vt] ?? stageColors.InBed
                    // x1 - x0 can be negative if the scale is inverted, use abs for safety
                    ctx.fillRect(x0, yTop, x1 - x0, yBot - yTop)
                }

                ctx.restore()
            },
        },
    }

    const opts: uPlot.Options = {
        width: containerRef.value.clientWidth || 800,
        height: props.height ?? 200,
        cursor: { show: false },
        legend: { show: false },
        scales: {
            x: { time: true },
            // Fixed y range covering all 5 lanes with half-unit padding on each side
            y: {
                range: () => [-0.5, 4.5] as [number, number],
            },
        },
        axes: [
            {
                // x-axis: HH:mm wall-clock labels derived from the local Date
                stroke: axisStroke,
                grid: { stroke: gridStroke },
                ticks: { stroke: axisStroke },
                values: (_u, splits) =>
                    splits.map((ts) => {
                        const d = new Date(ts * 1000)
                        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
                    }),
            },
            {
                // y-axis: fixed ticks at each lane value, labeled by stage name
                splits: () => [0, 1, 2, 3, 4] as number[],
                values: (_u, splits) => splits.map((v) => LANE_LABELS[v] ?? ''),
                size: 56,
                stroke: axisStroke,
                grid: { show: false },
                ticks: { show: false },
            },
        ],
        // Dummy series — keeps uPlot happy; all actual drawing is done in the plugin hook
        series: [
            {},
            { stroke: 'transparent', fill: 'transparent', points: { show: false }, width: 0 },
        ],
        plugins: [drawPlugin],
    }

    // x range comes from real sample boundaries; dummy y anchor at midpoint
    const data: uPlot.AlignedData = [
        [xMin, xMax],
        [2, 2],
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

// Recreate on prop changes or theme switch (same pattern as TrendChart.vue)
watch(
    () => [props.samples, props.height] as const,
    () => createChart(),
    { deep: true },
)
watch(isDark, () => createChart())
</script>

<style scoped>
.sleep-stage-chart {
    width: 100%;
}

.legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem 1.25rem;
    margin-top: 0.5rem;
    padding: 0 0.25rem;
}

.legend-item {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
}

.legend-swatch {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 2px;
    flex-shrink: 0;
}

.chart-empty {
    padding: 1.5rem;
    color: var(--color-muted-foreground);
    font-size: 0.875rem;
}
</style>
