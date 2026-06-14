<template>
    <div class="dashboard">
        <h1 class="page-title">Dashboard</h1>

        <!-- Summary Cards -->
        <div v-if="summary" class="summary-row">
            <StatCard label="Days with Data" :value="summary.days_with_data" :decimals="0" />
            <div class="stat-card-ahi">
                <StatCard label="Avg AHI" :value="summary.avg_ahi" :decimals="1" />
                <Tag
                    v-if="summary.effectiveness !== 'unknown'"
                    :value="summary.effectiveness"
                    :severity="effectivenessSeverity(summary.effectiveness)"
                    class="effectiveness-badge"
                />
            </div>
            <StatCard label="Avg Hours" :value="summary.avg_hours" unit="hrs" :decimals="1" />
            <StatCard label="Avg Leak" :value="summary.avg_leak" unit="L/min" :decimals="1" />
        </div>
        <div v-else-if="!loading" class="no-data">No therapy data available.</div>

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
            <DataTable
                :value="recentSessions"
                striped-rows
                selection-mode="single"
                class="cursor-pointer"
                @row-click="navigateToSession"
            >
                <Column header="Date">
                    <template #body="{ data }: { data: SessionListItem }">
                        {{ formatDateShort(data.start_time) }}
                    </template>
                </Column>
                <Column header="Duration" style="width: 90px">
                    <template #body="{ data }: { data: SessionListItem }">
                        {{ data.duration_hours.toFixed(1) }}h
                    </template>
                </Column>
                <Column header="AHI" style="width: 80px">
                    <template #body="{ data }: { data: SessionListItem }">
                        {{ data.ahi?.toFixed(1) ?? '---' }}
                    </template>
                </Column>
            </DataTable>
        </div>

        <div v-if="loading" class="loading-state">
            <i class="pi pi-spin pi-spinner" /> Loading dashboard...
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import StatCard from '@/components/StatCard.vue'
import TrendChart from '@/components/TrendChart.vue'
import CalendarHeatmap from '@/components/CalendarHeatmap.vue'
import { getSummary, getTrends } from '@/api/stats'
import { getDays } from '@/api/days'
import { getSessions } from '@/api/sessions'
import { useApiLoad } from '@/composables/useApiLoad'
import { formatDateShort } from '@/utils/formatting'
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
    ]
})

function effectivenessSeverity(e: string): string {
    const map: Record<string, string> = {
        excellent: 'success',
        good: 'success',
        fair: 'warn',
        poor: 'danger',
    }
    return map[e] ?? 'secondary'
}

function navigateToSession(event: { data: SessionListItem }): void {
    void router.push({ name: 'session-detail', params: { id: event.data.id } })
}

function onDayClick(date: string): void {
    void router.push({ name: 'sessions', query: { from: date, to: date } })
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
    color: var(--p-text-muted-color, #6b7280);
}

.cursor-pointer {
    cursor: pointer;
}
</style>
