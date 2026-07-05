<template>
    <div v-if="loading" class="loading-state">
        <Loader2 class="inline h-4 w-4 animate-spin" /> Loading events...
    </div>

    <ErrorState v-else-if="error" :message="error" :retry="reload" />

    <div v-else class="event-explorer">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <ArrowLeft class="inline h-4 w-4" /> Back to Session
        </RouterLink>

        <h1 class="page-title">Events — Session #{{ sessionId }}</h1>

        <!-- Summary -->
        <div class="summary-row">
            <StatCard label="Total Events" :value="filteredEvents.length" :decimals="0" />
            <StatCard label="Events/Hour" :value="eventsPerHour" :decimals="1" />
            <StatCard label="Types" :value="uniqueTypes.length" :decimals="0" />
        </div>

        <!-- Event Match (if analysis exists) -->
        <div v-if="matchResult" class="section-card">
            <h2>Machine vs Programmatic</h2>
            <div class="match-grid">
                <StatCard label="Machine Events" :value="matchResult.machine_count" :decimals="0" />
                <StatCard
                    label="Programmatic"
                    :value="matchResult.programmatic_count"
                    :decimals="0"
                />
                <StatCard label="Matched" :value="matchResult.matched" :decimals="0" />
                <StatCard
                    label="False Positives"
                    :value="matchResult.false_positives"
                    :decimals="0"
                />
                <StatCard
                    label="False Negatives"
                    :value="matchResult.false_negatives"
                    :decimals="0"
                />
                <StatCard label="Sensitivity" :value="sensitivity" unit="%" :decimals="1" />
            </div>
        </div>

        <!-- Filters -->
        <div class="filter-bar">
            <span class="filter-label text-muted-foreground">Filter by type:</span>
            <div class="type-chips">
                <button
                    v-for="t in uniqueTypes"
                    :key="t"
                    class="inline-flex cursor-pointer items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors"
                    :class="
                        activeTypes.has(t)
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted/50 text-muted-foreground opacity-50'
                    "
                    @click="toggleType(t)"
                >
                    {{ t }}
                </button>
            </div>
        </div>

        <!-- Event List -->
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead style="width: 80px">Type</TableHead>
                    <TableHead>Time</TableHead>
                    <TableHead style="width: 100px">Duration</TableHead>
                    <TableHead style="width: 100px">SpO₂ Drop</TableHead>
                    <TableHead style="width: 90px">Peak FL</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                <TableRow v-for="(row, i) in paginatedEvents" :key="i" class="odd:bg-muted/50">
                    <TableCell>
                        <span
                            class="event-badge"
                            :style="{ background: EVENT_COLORS[row.event_type] ?? '#ccc' }"
                        >
                            {{ row.event_type }}
                        </span>
                    </TableCell>
                    <TableCell>
                        <button
                            class="time-link text-primary"
                            @click="jumpToWaveform(row.offset_seconds)"
                        >
                            {{ formatTimeOffset(row.offset_seconds) }}
                        </button>
                    </TableCell>
                    <TableCell>{{ row.duration_seconds.toFixed(1) }}s</TableCell>
                    <TableCell>{{
                        row.spo2_drop != null ? row.spo2_drop.toFixed(1) + '%' : emDash
                    }}</TableCell>
                    <TableCell>{{
                        row.peak_flow_limitation != null
                            ? row.peak_flow_limitation.toFixed(2)
                            : emDash
                    }}</TableCell>
                </TableRow>
            </TableBody>
        </Table>

        <div v-if="totalPages > 1" class="flex items-center justify-between px-2 py-4">
            <span class="text-sm text-muted-foreground">
                Page {{ currentPage + 1 }} of {{ totalPages }}
            </span>
            <div class="flex gap-2">
                <Button
                    variant="outline"
                    size="sm"
                    :disabled="currentPage === 0"
                    @click="currentPage--"
                >
                    Previous
                </Button>
                <Button
                    variant="outline"
                    size="sm"
                    :disabled="currentPage >= totalPages - 1"
                    @click="currentPage++"
                >
                    Next
                </Button>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Loader2, ArrowLeft } from '@lucide/vue'
import StatCard from '@/components/StatCard.vue'
import { getSessionEvents, getEventMatch } from '@/api/events'
import { getSession } from '@/api/sessions'
import { useApiLoad } from '@/composables/useApiLoad'
import { formatTimeOffset } from '@/utils/formatting'
import { EVENT_COLORS } from '@/types'
import type { EventItem, EventMatchResult } from '@/types'
import ErrorState from '@/components/ErrorState.vue'

const emDash = '\u2014' // em-dash used for null display
const props = defineProps<{ sessionId: number }>()
const router = useRouter()

const activeTypes = ref<Set<string>>(new Set())
const currentPage = ref(0)
const pageSize = 50

const { data, loading, error, reload } = useApiLoad(async () => {
    const [events, session] = await Promise.all([
        getSessionEvents(props.sessionId),
        getSession(props.sessionId, false),
    ])
    let match: EventMatchResult | null = null
    try {
        match = await getEventMatch(props.sessionId)
    } catch {
        // No analysis — match panel hidden
    }
    return { events, duration: session.duration_hours, match }
}, 'Failed to load events')

const allEvents = computed<EventItem[]>(() => data.value?.events ?? [])
const matchResult = computed(() => data.value?.match ?? null)
const sessionDuration = computed(() => data.value?.duration ?? 0)

const uniqueTypes = computed(() => [...new Set(allEvents.value.map((e) => e.event_type))].sort())

const filteredEvents = computed(() => {
    if (activeTypes.value.size === 0) return allEvents.value
    return allEvents.value.filter((e) => activeTypes.value.has(e.event_type))
})

const paginatedEvents = computed(() => {
    const start = currentPage.value * pageSize
    return filteredEvents.value.slice(start, start + pageSize)
})

const totalPages = computed(() => Math.ceil(filteredEvents.value.length / pageSize))

const eventsPerHour = computed(() => {
    if (!sessionDuration.value) return null
    return filteredEvents.value.length / sessionDuration.value
})

const sensitivity = computed(() => {
    if (!matchResult.value || matchResult.value.machine_count === 0) return null
    return (matchResult.value.matched / matchResult.value.machine_count) * 100
})

watch(activeTypes, () => {
    currentPage.value = 0
})

function toggleType(type: string): void {
    const s = new Set(activeTypes.value)
    if (s.has(type)) s.delete(type)
    else s.add(type)
    activeTypes.value = s
}

function jumpToWaveform(offsetSec: number): void {
    void router.push({
        name: 'session-detail',
        params: { id: props.sessionId },
        query: { t: String(Math.floor(offsetSec)) },
    })
}
</script>

<style scoped>
.event-explorer {
    max-width: 1100px;
}

.summary-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1.25rem;
}

.match-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 0.5rem;
}

.filter-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
}

.type-chips {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.event-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-foreground);
}

.time-link {
    cursor: pointer;
    text-decoration: none;
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    color: inherit;
}
.time-link:hover {
    text-decoration: underline;
}
</style>
