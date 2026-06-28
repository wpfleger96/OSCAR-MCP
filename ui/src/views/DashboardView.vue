<template>
    <div class="dashboard">
        <h1 class="page-title">Dashboard</h1>

        <!-- Summary Cards -->
        <div v-if="summary" class="summary-row">
            <StatCard label="Days with Data" :value="summary.days_with_data" :decimals="0" />
            <div class="stat-card-ahi">
                <StatCard label="Avg AHI" :value="summary.avg_ahi" :decimals="1" />
                <Badge
                    v-if="summary.effectiveness !== 'unknown'"
                    v-bind="effectivenessBadgeAttrs(summary.effectiveness)"
                    class="effectiveness-badge"
                >
                    {{ summary.effectiveness }}
                </Badge>
            </div>
            <StatCard label="Avg Hours" :value="summary.avg_hours" unit="hrs" :decimals="1" />
            <StatCard label="Avg Leak" :value="summary.avg_leak" unit="L/min" :decimals="1" />
        </div>
        <div v-if="summary" class="summary-row">
            <StatCard label="Avg SpO₂" :value="summary.avg_spo2" unit="%" :decimals="1" />
            <StatCard label="Avg Pulse" :value="summary.avg_pulse" unit="bpm" :decimals="0" />
            <StatCard
                label="Avg Pressure"
                :value="summary.avg_pressure"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                label="Avg Resp Rate"
                :value="summary.avg_respiratory_rate"
                unit="br/min"
                :decimals="1"
            />
        </div>
        <div v-else-if="!loading" class="no-data">No therapy data available.</div>

        <div v-if="summary?.event_counts?.length" class="section-card">
            <h2>Event Breakdown</h2>
            <div class="event-breakdown">
                <div v-for="ec in summary.event_counts" :key="ec.event_type" class="event-item">
                    <Badge
                        :style="{
                            backgroundColor: eventColor(ec.event_type),
                            color: eventTextColor(ec.event_type),
                        }"
                    >
                        {{ ec.event_type }}
                    </Badge>
                    <span class="event-count">{{ ec.count }}</span>
                    <span class="text-muted-foreground text-sm"
                        >({{ ec.percentage?.toFixed(1) }}%)</span
                    >
                </div>
            </div>
        </div>

        <!-- AHI Trend Chart -->
        <div v-if="trendLabels.length" class="section-card">
            <h2>AHI Trend (Weekly)</h2>
            <TrendChart :labels="trendLabels" :datasets="trendDatasets" />
        </div>

        <!-- Calendar Heatmap -->
        <div v-if="days.length" class="section-card">
            <h2>Usage Calendar</h2>
            <CalendarHeatmap :days="days" @day-click="onDayClick" />
        </div>

        <!-- Recent Sessions -->
        <div v-if="recentSessions.length" class="section-card">
            <h2>Recent Sessions</h2>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Date</TableHead>
                        <TableHead class="w-[90px]">Duration</TableHead>
                        <TableHead class="w-[80px]">AHI</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    <TableRow
                        v-for="session in recentSessions"
                        :key="session.id"
                        class="cursor-pointer even:bg-muted/50 hover:bg-muted/50"
                        @click="navigateToSession(session)"
                    >
                        <TableCell>{{ formatDateShort(session.start_time) }}</TableCell>
                        <TableCell>{{ session.duration_hours.toFixed(1) }}h</TableCell>
                        <TableCell>{{ session.ahi?.toFixed(1) ?? '---' }}</TableCell>
                    </TableRow>
                </TableBody>
            </Table>
        </div>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-4 w-4 animate-spin" /> Loading dashboard...
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2 } from '@lucide/vue'
import { Badge } from '@/components/ui/badge'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import StatCard from '@/components/StatCard.vue'
import TrendChart from '@/components/TrendChart.vue'
import CalendarHeatmap from '@/components/CalendarHeatmap.vue'
import { getSummary, getTrends } from '@/api/stats'
import { getDays } from '@/api/days'
import { getSessions } from '@/api/sessions'
import { useApiLoad } from '@/composables/useApiLoad'
import { formatDateShort } from '@/utils/formatting'
import { EVENT_COLORS } from '@/types'
import type { SessionListItem } from '@/types'

const router = useRouter()

// Dashboard gracefully shows empty sections on error, so `error` is unused.
const { data, loading } = useApiLoad(async () => {
    const [summary, trends, daysResult, sessionsResult] = await Promise.all([
        getSummary(),
        getTrends('week'),
        getDays({ limit: 365 }),
        getSessions({ limit: 5, sort_by: 'date-desc' }),
    ])
    return { summary, trends, days: daysResult.items, recentSessions: sessionsResult.items }
})

const summary = computed(() => data.value?.summary ?? null)
const trends = computed(() => data.value?.trends ?? null)
const days = computed(() => data.value?.days ?? [])
const recentSessions = computed(() => data.value?.recentSessions ?? [])

const trendLabels = computed(() => trends.value?.ahi.map((t) => t[0]) ?? [])
const trendDatasets = computed(() => {
    if (!trends.value) return []
    return [
        { label: 'AHI', values: trends.value.ahi.map((t) => t[1]), color: '#2563eb' },
        { label: 'Usage (hrs)', values: trends.value.usage.map((t) => t[1]), color: '#16a34a' },
        { label: 'SpO₂ (%)', values: trends.value.spo2.map((t) => t[1]), color: '#f59e0b' },
        { label: 'Leak (L/min)', values: trends.value.leak.map((t) => t[1]), color: '#ef4444' },
    ]
})

function effectivenessBadgeAttrs(e: string): Record<string, string> {
    if (e === 'excellent' || e === 'good')
        return { class: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200' }
    if (e === 'fair')
        return { class: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200' }
    if (e === 'poor') return { variant: 'destructive' }
    return { variant: 'secondary' }
}

function navigateToSession(session: SessionListItem): void {
    void router.push({ name: 'session-detail', params: { id: session.id } })
}

function onDayClick(date: string): void {
    void router.push({ name: 'day-detail', params: { date } })
}

function eventColor(type: string): string {
    return EVENT_COLORS[type] ?? 'rgba(156, 163, 175, 0.25)'
}

function eventTextColor(type: string): string {
    return EVENT_COLORS[type] ? 'inherit' : 'var(--color-muted-foreground)'
}
</script>

<style scoped>
.dashboard {
    max-width: 1200px;
}

.summary-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}

.stat-card-ahi {
    position: relative;
}

.effectiveness-badge {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
}

.no-data {
    padding: 2rem;
    text-align: center;
    color: var(--color-muted-foreground);
}

.event-breakdown {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
}

.event-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.event-count {
    font-weight: 600;
}
</style>
