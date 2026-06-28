<template>
    <div v-if="loading" class="loading-state">
        <Loader2 class="inline h-4 w-4 animate-spin" /> Loading analysis...
    </div>

    <div v-else-if="error" class="error-state">
        <AlertTriangle class="inline h-4 w-4" /> {{ error }}
    </div>

    <div v-else-if="noAnalysis" class="no-analysis">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <ArrowLeft class="inline h-4 w-4" /> Back to Session
        </RouterLink>
        <div class="empty-card border border-border bg-card">
            <BarChart3 class="empty-icon text-muted-foreground" />
            <p class="text-muted-foreground">No analysis results for this session.</p>
            <Button :disabled="running" @click="handleRunAnalysis">
                <Loader2 v-if="running" class="h-4 w-4 animate-spin" />
                <Play v-else class="h-4 w-4" />
                Run Analysis
            </Button>
        </div>
    </div>

    <div v-else-if="analysis" class="analysis-view">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <ArrowLeft class="inline h-4 w-4" /> Back to Session
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
                :value="analysis.machine_events?.length ?? 0"
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
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Mode</TableHead>
                        <TableHead style="width: 80px">AHI</TableHead>
                        <TableHead style="width: 80px">RDI</TableHead>
                        <TableHead style="width: 80px">Apneas</TableHead>
                        <TableHead style="width: 100px">Hypopneas</TableHead>
                        <TableHead style="width: 80px">RERAs</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    <TableRow v-for="(row, i) in modeRows" :key="i" class="odd:bg-muted/50">
                        <TableCell>{{ row.mode }}</TableCell>
                        <TableCell
                            ><strong>{{ row.ahi.toFixed(1) }}</strong></TableCell
                        >
                        <TableCell>{{ row.rdi.toFixed(1) }}</TableCell>
                        <TableCell>{{ row.apneas }}</TableCell>
                        <TableCell>{{ row.hypopneas }}</TableCell>
                        <TableCell>{{ row.reras }}</TableCell>
                    </TableRow>
                </TableBody>
            </Table>
        </div>

        <!-- Per-mode Events -->
        <div class="section-card">
            <h2>Events by Mode</h2>
            <ToggleGroup
                :model-value="selectedMode"
                type="single"
                variant="outline"
                class="mode-selector"
                @update:model-value="
                    (v) => {
                        if (v) selectedMode = v as string
                    }
                "
            >
                <ToggleGroupItem v-for="mode in modeOptions" :key="mode" :value="mode">
                    {{ mode }}
                </ToggleGroupItem>
            </ToggleGroup>

            <div v-if="selectedModeResult">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead style="width: 80px">Type</TableHead>
                            <TableHead>Start Time</TableHead>
                            <TableHead style="width: 90px">Duration</TableHead>
                            <TableHead style="width: 100px">Flow Red.</TableHead>
                            <TableHead style="width: 100px">Confidence</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <TableRow
                            v-for="(row, i) in paginatedEvents"
                            :key="i"
                            class="odd:bg-muted/50"
                        >
                            <TableCell>
                                <span
                                    class="event-badge"
                                    :style="{ background: EVENT_COLORS[row.type] ?? '#ddd' }"
                                >
                                    {{ row.type }}
                                </span>
                            </TableCell>
                            <TableCell>{{ formatTimeOffset(row.start) }}</TableCell>
                            <TableCell>{{ row.duration.toFixed(1) }}s</TableCell>
                            <TableCell>{{ (row.flowReduction * 100).toFixed(0) }}%</TableCell>
                            <TableCell>{{ (row.confidence * 100).toFixed(0) }}%</TableCell>
                        </TableRow>
                    </TableBody>
                </Table>

                <div v-if="totalEventPages > 1" class="flex items-center justify-between px-2 py-4">
                    <span class="text-sm text-muted-foreground">
                        Page {{ eventsPage + 1 }} of {{ totalEventPages }}
                    </span>
                    <div class="flex gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            :disabled="eventsPage === 0"
                            @click="eventsPage--"
                        >
                            Previous
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            :disabled="eventsPage >= totalEventPages - 1"
                            @click="eventsPage++"
                        >
                            Next
                        </Button>
                    </div>
                </div>
            </div>
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

        <!-- Event Comparison -->
        <div v-if="comparison" class="section-card">
            <h2>Event Comparison</h2>
            <div class="summary-row" style="margin-bottom: 1rem">
                <StatCard
                    label="Machine Events"
                    :value="comparison.machine_event_count"
                    :decimals="0"
                />
                <StatCard
                    label="Programmatic Events"
                    :value="comparison.programmatic_event_count"
                    :decimals="0"
                />
                <StatCard
                    label="False Negatives"
                    :value="comparison.false_negatives?.length ?? 0"
                    :decimals="0"
                />
                <StatCard
                    label="False Positives"
                    :value="
                        (comparison.false_positives_apnea?.length ?? 0) +
                        (comparison.false_positives_hypopnea?.length ?? 0)
                    "
                    :decimals="0"
                />
            </div>

            <div v-if="comparison.false_negatives?.length" class="compare-table-section">
                <h3>False Negatives (machine events missed by programmatic)</h3>
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead style="width: 80px">Type</TableHead>
                            <TableHead>Time</TableHead>
                            <TableHead style="width: 90px">Duration</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <TableRow
                            v-for="(e, i) in comparison.false_negatives"
                            :key="'fn-' + i"
                            class="odd:bg-muted/50"
                        >
                            <TableCell>
                                <span
                                    class="event-badge"
                                    :style="{ background: EVENT_COLORS[e.event_type] ?? '#ddd' }"
                                    >{{ e.event_type }}</span
                                >
                            </TableCell>
                            <TableCell>
                                <RouterLink
                                    :to="{
                                        name: 'session-detail',
                                        params: { id: sessionId },
                                        query: { t: e.start_time },
                                    }"
                                    class="text-primary hover:underline"
                                >
                                    {{ formatTimeOffset(e.start_time) }}
                                </RouterLink>
                            </TableCell>
                            <TableCell>{{ e.duration.toFixed(1) }}s</TableCell>
                        </TableRow>
                    </TableBody>
                </Table>
            </div>

            <div
                v-if="
                    comparison.false_positives_apnea?.length ||
                    comparison.false_positives_hypopnea?.length
                "
                class="compare-table-section"
            >
                <h3>False Positives (programmatic events not in machine)</h3>
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead style="width: 80px">Type</TableHead>
                            <TableHead>Time</TableHead>
                            <TableHead style="width: 90px">Duration</TableHead>
                            <TableHead style="width: 100px">Confidence</TableHead>
                            <TableHead style="width: 100px">Flow Red.</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <TableRow
                            v-for="(e, i) in allFalsePositives"
                            :key="'fp-' + i"
                            class="odd:bg-muted/50"
                        >
                            <TableCell>
                                <span
                                    class="event-badge"
                                    :style="{ background: EVENT_COLORS[e.event_type] ?? '#ddd' }"
                                    >{{ e.event_type }}</span
                                >
                            </TableCell>
                            <TableCell>
                                <RouterLink
                                    :to="{
                                        name: 'session-detail',
                                        params: { id: sessionId },
                                        query: { t: e.start_time },
                                    }"
                                    class="text-primary hover:underline"
                                >
                                    {{ formatTimeOffset(e.start_time) }}
                                </RouterLink>
                            </TableCell>
                            <TableCell>{{ e.duration.toFixed(1) }}s</TableCell>
                            <TableCell>{{
                                e.confidence != null ? (e.confidence * 100).toFixed(0) + '%' : '---'
                            }}</TableCell>
                            <TableCell>{{
                                e.flow_reduction != null
                                    ? (e.flow_reduction * 100).toFixed(0) + '%'
                                    : '---'
                            }}</TableCell>
                        </TableRow>
                    </TableBody>
                </Table>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Loader2, AlertTriangle, ArrowLeft, BarChart3, Play } from '@lucide/vue'
import StatCard from '@/components/StatCard.vue'
import { getAnalysis, runAnalysis } from '@/api/analysis'
import { getWaveformCompare } from '@/api/waveforms'
import { formatTimeOffset } from '@/utils/formatting'
import { EVENT_COLORS } from '@/types'
import type { AnalysisResult, EventComparisonResult } from '@/types'

const props = defineProps<{ sessionId: number }>()

const loading = ref(true)
const error = ref<string | null>(null)
const noAnalysis = ref(false)
const running = ref(false)
const analysis = ref<AnalysisResult | null>(null)
const comparison = ref<EventComparisonResult | null>(null)
const selectedMode = ref('')
const eventsPage = ref(0)
const eventsPageSize = 25

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
    return Object.entries(analysis.value.mode_results ?? {}).map(([name, r]) => ({
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
    for (const a of r.apneas ?? []) {
        events.push({
            type: a.event_type,
            start: a.start_time,
            duration: a.duration,
            flowReduction: a.flow_reduction,
            confidence: a.confidence,
        })
    }
    for (const h of r.hypopneas ?? []) {
        events.push({
            type: 'H',
            start: h.start_time,
            duration: h.duration,
            flowReduction: h.flow_reduction,
            confidence: h.confidence,
        })
    }
    for (const re of r.reras ?? []) {
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

const paginatedEvents = computed(() => {
    const start = eventsPage.value * eventsPageSize
    return modeEvents.value.slice(start, start + eventsPageSize)
})

const totalEventPages = computed(() => Math.ceil(modeEvents.value.length / eventsPageSize))

const allFalsePositives = computed(() =>
    [
        ...(comparison.value?.false_positives_apnea ?? []),
        ...(comparison.value?.false_positives_hypopnea ?? []),
    ].sort((a, b) => a.start_time - b.start_time),
)

watch(selectedMode, () => {
    eventsPage.value = 0
})

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

// useApiLoad skipped — 404 routes to noAnalysis state, not generic error
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

    try {
        comparison.value = await getWaveformCompare(props.sessionId)
    } catch {
        // Comparison data not available — section won't render
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
    border-radius: 8px;
}

.empty-icon {
    width: 2.5rem;
    height: 2.5rem;
    margin: 0 auto 1rem;
    display: block;
}

.empty-card p {
    margin-bottom: 1rem;
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

.compare-table-section {
    margin-top: 1rem;
}

.compare-table-section h3 {
    font-size: 0.95rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}
</style>
