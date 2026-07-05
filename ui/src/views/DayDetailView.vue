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
            <StatCard label="Obstructive Apneas" :value="data.obstructive_apneas" :decimals="0" />
            <StatCard label="Central Apneas" :value="data.central_apneas" :decimals="0" />
            <StatCard label="Hypopneas" :value="data.hypopneas" :decimals="0" />
            <StatCard label="RERAs" :value="data.reras" :decimals="0" />
        </div>

        <!-- Pressure group -->
        <div
            v-if="
                data.avg_pressure != null || data.pressure_min != null || data.pressure_95th != null
            "
            class="stats-grid mb-6"
        >
            <StatCard
                v-if="data.avg_pressure != null"
                label="Pressure Mean"
                :value="data.avg_pressure"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.pressure_min != null"
                label="Pressure Min"
                :value="data.pressure_min"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.pressure_max != null"
                label="Pressure Max"
                :value="data.pressure_max"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.pressure_median != null"
                label="Pressure Median"
                :value="data.pressure_median"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.pressure_95th != null"
                label="Pressure 95th"
                :value="data.pressure_95th"
                unit="cmH₂O"
                :decimals="1"
            />
        </div>

        <!-- EPAP group -->
        <div
            v-if="data.epap_min != null || data.epap_mean != null || data.epap_95th != null"
            class="stats-grid mb-6"
        >
            <StatCard
                v-if="data.epap_mean != null"
                label="EPAP Mean"
                :value="data.epap_mean"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.epap_min != null"
                label="EPAP Min"
                :value="data.epap_min"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.epap_max != null"
                label="EPAP Max"
                :value="data.epap_max"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.epap_median != null"
                label="EPAP Median"
                :value="data.epap_median"
                unit="cmH₂O"
                :decimals="1"
            />
            <StatCard
                v-if="data.epap_95th != null"
                label="EPAP 95th"
                :value="data.epap_95th"
                unit="cmH₂O"
                :decimals="1"
            />
        </div>

        <!-- Leak group -->
        <div
            v-if="data.avg_leak != null || data.leak_min != null || data.leak_95th != null"
            class="stats-grid mb-6"
        >
            <StatCard
                v-if="data.avg_leak != null"
                label="Leak Mean"
                :value="data.avg_leak"
                unit="L/min"
                :decimals="1"
            />
            <StatCard
                v-if="data.leak_min != null"
                label="Leak Min"
                :value="data.leak_min"
                unit="L/min"
                :decimals="1"
            />
            <StatCard
                v-if="data.leak_max != null"
                label="Leak Max"
                :value="data.leak_max"
                unit="L/min"
                :decimals="1"
            />
            <StatCard
                v-if="data.leak_95th != null"
                label="Leak 95th"
                :value="data.leak_95th"
                unit="L/min"
                :decimals="1"
            />
        </div>

        <!-- SpO₂ group -->
        <div
            v-if="data.avg_spo2 != null || data.spo2_min != null || data.spo2_max != null"
            class="stats-grid mb-6"
        >
            <StatCard
                v-if="data.avg_spo2 != null"
                label="SpO₂ Mean"
                :value="data.avg_spo2"
                unit="%"
                :decimals="1"
            />
            <StatCard
                v-if="data.spo2_min != null"
                label="SpO₂ Min"
                :value="data.spo2_min"
                unit="%"
                :decimals="1"
            />
            <StatCard
                v-if="data.spo2_max != null"
                label="SpO₂ Max"
                :value="data.spo2_max"
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
