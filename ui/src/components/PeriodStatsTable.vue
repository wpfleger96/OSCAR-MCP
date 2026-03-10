<template>
    <DataTable :value="periods" :loading="loading" striped-rows>
        <template #empty>
            <div class="table-empty">No period data available.</div>
        </template>
        <Column header="Period">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ formatDate(data.period_start) }} – {{ formatDate(data.period_end) }}
            </template>
        </Column>
        <Column field="days_used" header="Days Used" style="width: 90px" />
        <Column header="Avg Hours" style="width: 90px">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ data.avg_hours_per_day?.toFixed(1) ?? '---' }}
            </template>
        </Column>
        <Column header="Avg AHI" style="width: 90px">
            <template #body="{ data }: { data: PeriodStatistics }">
                <span :class="ahiClass(data.avg_ahi)">
                    {{ data.avg_ahi?.toFixed(1) ?? '---' }}
                </span>
            </template>
        </Column>
        <Column header="Median AHI" style="width: 100px">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ data.median_ahi?.toFixed(1) ?? '---' }}
            </template>
        </Column>
        <Column header="Avg Pressure" style="width: 110px">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ data.avg_pressure?.toFixed(1) ?? '---' }}
            </template>
        </Column>
        <Column header="Avg Leak" style="width: 90px">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ data.avg_leak?.toFixed(1) ?? '---' }}
            </template>
        </Column>
        <Column header="Avg SpO₂" style="width: 90px">
            <template #body="{ data }: { data: PeriodStatistics }">
                {{ data.avg_spo2?.toFixed(1) ?? '---' }}
            </template>
        </Column>
    </DataTable>
</template>

<script setup lang="ts">
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import type { PeriodStatistics } from '@/types'
import { ahiClass } from '@/utils/format'

defineProps<{
    periods: PeriodStatistics[]
    loading: boolean
}>()

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
</script>

<style scoped>
.table-empty {
    padding: 2rem;
    text-align: center;
    color: var(--p-text-muted-color, #6b7280);
}
</style>
