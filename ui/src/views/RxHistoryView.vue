<template>
    <div class="rx-history">
        <h1 class="page-title">RX History</h1>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-4 w-4 animate-spin" /> Loading RX data...
        </div>

        <div v-else-if="error" class="error-state">
            <AlertTriangle class="h-4 w-4" /> {{ error }}
        </div>

        <template v-else-if="history.length">
            <!-- Current Settings -->
            <div v-if="current" class="section-card">
                <h2>Current Settings</h2>
                <div class="current-meta">
                    <span
                        >{{ formatDateFull(current.start_date) }} –
                        {{ formatDateFull(current.end_date) }}</span
                    >
                    <span>{{ current.days_count }} days</span>
                    <span v-if="current.avg_ahi != null"
                        >Avg AHI: {{ current.avg_ahi.toFixed(1) }}</span
                    >
                    <span v-if="current.avg_hours != null"
                        >Avg {{ current.avg_hours.toFixed(1) }} hrs/night</span
                    >
                </div>
                <div class="settings-pills">
                    <Badge
                        v-for="(value, key) in current.settings"
                        :key="key"
                        variant="secondary"
                        class="setting-pill"
                    >
                        {{ key }}: {{ value }}
                    </Badge>
                </div>
            </div>

            <!-- Comparison Table -->
            <div v-if="comparison" class="section-card">
                <h2>Period Comparison</h2>
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Period</TableHead>
                            <TableHead class="w-[70px]">Days</TableHead>
                            <TableHead>Settings</TableHead>
                            <TableHead class="w-[90px]">Avg AHI</TableHead>
                            <TableHead class="w-[100px]">Median AHI</TableHead>
                            <TableHead class="w-[90px]">Avg Hours</TableHead>
                            <TableHead class="w-[90px]">Avg Leak</TableHead>
                            <TableHead class="w-[80px]"></TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <TableRow
                            v-for="row in comparisonRows"
                            :key="row.start_date"
                            class="even:bg-muted/50"
                            :class="{
                                'bg-[rgba(34,197,94,0.08)]': row.isBest,
                                'bg-[rgba(239,68,68,0.08)]': row.isWorst,
                            }"
                        >
                            <TableCell>
                                {{ formatDateFull(row.start_date) }} –
                                {{ formatDateFull(row.end_date) }}
                            </TableCell>
                            <TableCell>{{ row.days_count }}</TableCell>
                            <TableCell>{{ summarizeSettings(row.settings) }}</TableCell>
                            <TableCell>{{ row.avg_ahi?.toFixed(1) ?? '---' }}</TableCell>
                            <TableCell>{{ row.median_ahi?.toFixed(1) ?? '---' }}</TableCell>
                            <TableCell>{{ row.avg_hours?.toFixed(1) ?? '---' }}</TableCell>
                            <TableCell>{{ row.avg_leak?.toFixed(1) ?? '---' }}</TableCell>
                            <TableCell>
                                <Badge
                                    v-if="row.isBest"
                                    class="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
                                    >Best</Badge
                                >
                                <Badge v-if="row.isWorst" variant="destructive">Worst</Badge>
                            </TableCell>
                        </TableRow>
                    </TableBody>
                </Table>
            </div>
        </template>

        <div v-else class="no-data"><Info class="h-4 w-4" /> No prescription data available.</div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Loader2, AlertTriangle, Info } from '@lucide/vue'
import { Badge } from '@/components/ui/badge'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { getRxHistory, getRxCurrent, getRxCompare } from '@/api/rx'
import { useApiLoad } from '@/composables/useApiLoad'
import { formatDateFull } from '@/utils/formatting'
import type { RxPeriodResponse } from '@/types'

const { data, loading, error } = useApiLoad(async () => {
    const [history, current, comparison] = await Promise.all([
        getRxHistory(),
        getRxCurrent(),
        getRxCompare(),
    ])
    return { history, current, comparison }
}, 'Failed to load RX data')

const history = computed(() => data.value?.history ?? [])
const current = computed(() => data.value?.current ?? null)
const comparison = computed(() => data.value?.comparison ?? null)

interface ComparisonRow extends RxPeriodResponse {
    isBest: boolean
    isWorst: boolean
}

const comparisonRows = computed<ComparisonRow[]>(() => {
    if (!comparison.value) return []
    return comparison.value.periods.map((p, i) => ({
        ...p,
        isBest: comparison.value!.best_index === i,
        isWorst: comparison.value!.worst_index === i,
    }))
})

function summarizeSettings(settings: Record<string, string>): string {
    const keys = ['Mode', 'Pressure', 'EPR', 'EPR Level']
    const parts: string[] = []
    for (const k of keys) {
        if (k in settings) parts.push(`${k}: ${settings[k]}`)
    }
    if (!parts.length) {
        return Object.entries(settings)
            .slice(0, 3)
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ')
    }
    return parts.join(', ')
}
</script>

<style scoped>
.rx-history {
    max-width: 1200px;
}

.no-data {
    padding: 2rem;
    text-align: center;
    color: var(--color-muted-foreground);
}

.current-meta {
    display: flex;
    gap: 1.25rem;
    flex-wrap: wrap;
    font-size: 0.9rem;
    color: var(--color-muted-foreground);
    margin-bottom: 0.75rem;
}

.settings-pills {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.setting-pill {
    font-size: 0.78rem;
}
</style>
