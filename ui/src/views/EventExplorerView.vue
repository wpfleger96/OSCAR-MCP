<template>
    <div v-if="loading" class="loading-state">
        <i class="pi pi-spin pi-spinner" /> Loading events...
    </div>

    <div v-else-if="error" class="error-state">
        <i class="pi pi-exclamation-triangle" /> {{ error }}
    </div>

    <div v-else class="event-explorer">
        <RouterLink :to="{ name: 'session-detail', params: { id: sessionId } }" class="back-link">
            <i class="pi pi-arrow-left" /> Back to Session
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
            <span class="filter-label">Filter by type:</span>
            <div class="type-chips">
                <Tag
                    v-for="t in uniqueTypes"
                    :key="t"
                    :value="t"
                    :class="{
                        'tag-active': activeTypes.has(t),
                        'tag-inactive': !activeTypes.has(t),
                    }"
                    class="type-tag"
                    @click="toggleType(t)"
                />
            </div>
        </div>

        <!-- Event List -->
        <DataTable :value="filteredEvents" striped-rows :rows="50" paginator>
            <Column header="Type" style="width: 80px">
                <template #body="{ data }: { data: EventItem }">
                    <span
                        class="event-badge"
                        :style="{ background: EVENT_COLORS[data.event_type] ?? '#ccc' }"
                    >
                        {{ data.event_type }}
                    </span>
                </template>
            </Column>
            <Column header="Time">
                <template #body="{ data }: { data: EventItem }">
                    <a class="time-link" @click="jumpToWaveform(data.offset_seconds)">
                        {{ formatTime(data.offset_seconds) }}
                    </a>
                </template>
            </Column>
            <Column header="Duration" style="width: 100px">
                <template #body="{ data }: { data: EventItem }">
                    {{ data.duration_seconds.toFixed(1) }}s
                </template>
            </Column>
        </DataTable>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import StatCard from '@/components/StatCard.vue'
import { getSessionEvents, getEventMatch } from '@/api/events'
import { getSession } from '@/api/sessions'
import { EVENT_COLORS } from '@/types'
import type { EventItem, EventMatchResult } from '@/types'

const props = defineProps<{ sessionId: number }>()
const router = useRouter()

const loading = ref(true)
const error = ref<string | null>(null)
const allEvents = ref<EventItem[]>([])
const activeTypes = ref<Set<string>>(new Set())
const matchResult = ref<EventMatchResult | null>(null)
const sessionDuration = ref(0)

const uniqueTypes = computed(() => [...new Set(allEvents.value.map((e) => e.event_type))].sort())

const filteredEvents = computed(() => {
    if (activeTypes.value.size === 0) return allEvents.value
    return allEvents.value.filter((e) => activeTypes.value.has(e.event_type))
})

const eventsPerHour = computed(() => {
    if (!sessionDuration.value) return null
    return filteredEvents.value.length / sessionDuration.value
})

const sensitivity = computed(() => {
    if (!matchResult.value || matchResult.value.machine_count === 0) return null
    return (matchResult.value.matched / matchResult.value.machine_count) * 100
})

function toggleType(type: string): void {
    const s = new Set(activeTypes.value)
    if (s.has(type)) s.delete(type)
    else s.add(type)
    activeTypes.value = s
}

function formatTime(secs: number): string {
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    const s = Math.floor(secs % 60)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function jumpToWaveform(offsetSec: number): void {
    void router.push({
        name: 'session-detail',
        params: { id: props.sessionId },
        query: { t: String(Math.floor(offsetSec)) },
    })
}

onMounted(async () => {
    try {
        const [events, session] = await Promise.all([
            getSessionEvents(props.sessionId),
            getSession(props.sessionId, false),
        ])
        allEvents.value = events
        sessionDuration.value = session.duration_hours

        // Try to load event match data
        try {
            matchResult.value = await getEventMatch(props.sessionId)
        } catch {
            // No analysis — match panel hidden
        }
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to load events'
    } finally {
        loading.value = false
    }
})
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

.filter-label {
    font-size: 0.85rem;
    color: var(--p-text-muted-color, #6b7280);
}

.type-chips {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.type-tag {
    cursor: pointer;
    user-select: none;
}

.tag-inactive {
    opacity: 0.4;
}

.event-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    color: #1a1a1a;
}

.time-link {
    color: var(--p-primary-color, #3b82f6);
    cursor: pointer;
    text-decoration: none;
}
.time-link:hover {
    text-decoration: underline;
}
</style>
