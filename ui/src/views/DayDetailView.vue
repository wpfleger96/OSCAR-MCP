<template>
    <div v-if="loading" class="loading-state">
        <Loader2 class="inline h-4 w-4 animate-spin" /> Loading day...
    </div>

    <ErrorState v-else-if="error" :message="error" :retry="reload" />

    <div v-else-if="data" class="day-detail">
        <RouterLink to="/" class="back-link">
            <ArrowLeft class="inline h-4 w-4" /> Dashboard
        </RouterLink>

        <div class="day-header">
            <h1>{{ formatDateWithWeekday(data.date) }}</h1>
        </div>

        <div class="stats-grid mb-6">
            <StatCard
                label="Total Hours"
                :value="data.total_therapy_hours ?? null"
                unit="hr"
                :decimals="1"
            />
            <StatCard label="AHI" :value="data.ahi ?? null" :decimals="1" />
            <StatCard label="Sessions" :value="data.session_count" :decimals="0" />
            <StatCard label="OAI" :value="data.oai ?? null" :decimals="2" />
            <StatCard label="CAI" :value="data.cai ?? null" :decimals="2" />
            <StatCard label="HI" :value="data.hi ?? null" :decimals="2" />
        </div>

        <div
            v-if="data.avg_pressure != null || data.avg_leak != null || data.avg_spo2 != null"
            class="stats-grid mb-6"
        >
            <StatCard
                v-if="data.avg_pressure != null"
                label="Avg Pressure"
                :value="data.avg_pressure"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.avg_leak != null"
                label="Avg Leak"
                :value="data.avg_leak"
                unit="L/min"
                :decimals="1"
            />
            <StatCard
                v-if="data.avg_spo2 != null"
                label="Avg SpO₂"
                :value="data.avg_spo2"
                unit="%"
                :decimals="1"
            />
        </div>

        <div v-if="data.session_ids?.length" class="sessions-section">
            <h2>Sessions</h2>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>#</TableHead>
                        <TableHead>Session ID</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    <TableRow v-for="(id, i) in data.session_ids" :key="id">
                        <TableCell class="text-muted-foreground">{{ i + 1 }}</TableCell>
                        <TableCell>
                            <RouterLink :to="`/sessions/${id}`" class="session-link">
                                {{ id }}
                            </RouterLink>
                        </TableCell>
                    </TableRow>
                </TableBody>
            </Table>
        </div>
    </div>
</template>

<script setup lang="ts">
import StatCard from '@/components/StatCard.vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Loader2, ArrowLeft } from '@lucide/vue'
import { useApiLoad } from '@/composables/useApiLoad'
import { getDay } from '@/api/days'
import { formatDateWithWeekday } from '@/utils/formatting'
import ErrorState from '@/components/ErrorState.vue'

const props = defineProps<{ dayDate: string }>()

const { data, loading, error, reload } = useApiLoad(() => getDay(props.dayDate))
</script>

<style scoped>
.day-detail {
    max-width: 1000px;
}

.day-header {
    margin-bottom: 1.5rem;
}

.day-header h1 {
    font-size: 1.5rem;
    font-weight: 600;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.75rem;
}

.sessions-section h2 {
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
}

.session-link {
    color: var(--color-primary);
}

.session-link:hover {
    text-decoration: underline;
}
</style>
