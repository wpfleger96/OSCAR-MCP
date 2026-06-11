<template>
    <div class="stats-view">
        <h1 class="page-title">Statistics</h1>

        <!-- Period Selector -->
        <div class="period-selector">
            <SelectButton
                v-model="periodType"
                :options="periodOptions"
                option-label="label"
                option-value="value"
            />
        </div>

        <!-- Period Stats Table -->
        <div class="section-card">
            <h2>Period Breakdown</h2>
            <PeriodStatsTable :periods="periods" :loading="periodsLoading" />
        </div>

        <!-- Trend Chart -->
        <div v-if="trendLabels.length" class="section-card">
            <h2>Trends</h2>
            <TrendChart :labels="trendLabels" :datasets="trendDatasets" />
        </div>

        <!-- Records -->
        <div class="section-card">
            <h2>Records</h2>
            <RecordsPanel :records="records" :loading="recordsLoading" />
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import SelectButton from 'primevue/selectbutton'
import PeriodStatsTable from '@/components/PeriodStatsTable.vue'
import TrendChart from '@/components/TrendChart.vue'
import RecordsPanel from '@/components/RecordsPanel.vue'
import { getPeriods, getTrends, getRecords } from '@/api/stats'
import { useApiLoad } from '@/composables/useApiLoad'
import type { PeriodStatistics } from '@/types'

const periodOptions = [
    { label: 'Week', value: 'week' },
    { label: 'Month', value: 'month' },
    { label: '6 Month', value: '6month' },
    { label: 'Year', value: 'year' },
]

const periodType = ref('month')

const {
    data: periodData,
    loading: periodsLoading,
    reload: reloadPeriods,
} = useApiLoad(async () => {
    const [periods, trends] = await Promise.all([
        getPeriods(periodType.value),
        getTrends(periodType.value),
    ])
    return { periods, trends }
})

const { data: records, loading: recordsLoading } = useApiLoad(() => getRecords())

const periods = computed<PeriodStatistics[]>(() => periodData.value?.periods ?? [])
const trends = computed(() => periodData.value?.trends ?? null)

watch(periodType, () => void reloadPeriods())

const trendLabels = computed(() => trends.value?.ahi.map((t) => t[0]) ?? [])
const trendDatasets = computed(() => {
    if (!trends.value) return []
    return [
        { label: 'AHI', values: trends.value.ahi.map((t) => t[1]), color: '#2563eb' },
        { label: 'Usage (hrs)', values: trends.value.usage.map((t) => t[1]), color: '#16a34a' },
        { label: 'SpO₂ (%)', values: trends.value.spo2.map((t) => t[1]), color: '#f97316' },
        { label: 'Leak (L/min)', values: trends.value.leak.map((t) => t[1]), color: '#dc2626' },
    ]
})
</script>

<style scoped>
.stats-view {
    max-width: 1200px;
}

.period-selector {
    margin-bottom: 1.25rem;
}
</style>
