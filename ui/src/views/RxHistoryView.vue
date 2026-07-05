<template>
    <div class="rx-history">
        <h1 class="page-title">RX History</h1>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-4 w-4 animate-spin" /> Loading RX data...
        </div>

        <ErrorState v-else-if="error" :message="error" :retry="reload" />

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
                    <span v-if="current.device_name">{{ current.device_name }}</span>
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
                        {{ settingLabel(key) }}: {{ formatSettingValue(key, value) }}
                    </Badge>
                </div>
            </div>

            <!-- Comparison Table -->
            <div v-if="comparison" class="section-card">
                <h2>Period Comparison</h2>
                <div class="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead class="whitespace-nowrap">Period</TableHead>
                                <TableHead class="whitespace-nowrap">Days</TableHead>
                                <TableHead class="whitespace-nowrap">Device</TableHead>
                                <TableHead>Settings</TableHead>
                                <TableHead class="whitespace-nowrap">Avg AHI</TableHead>
                                <TableHead class="whitespace-nowrap">Median AHI</TableHead>
                                <TableHead class="whitespace-nowrap">Avg Hours</TableHead>
                                <TableHead class="whitespace-nowrap">Avg Leak</TableHead>
                                <TableHead></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow
                                v-for="row in comparisonRows"
                                :key="row.start_date"
                                :class="{
                                    'bg-green-500/10': row.isBest,
                                    'bg-destructive/10': row.isWorst,
                                    'even:bg-muted/50': !row.isBest && !row.isWorst,
                                }"
                            >
                                <TableCell class="whitespace-nowrap">
                                    {{ formatDateFull(row.start_date) }} –
                                    {{ formatDateFull(row.end_date) }}
                                </TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.days_count
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.device_name ?? '—'
                                }}</TableCell>
                                <TableCell>{{ summarizeSettings(row.settings) }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_ahi?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.median_ahi?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_hours?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_leak?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">
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
            </div>

            <!-- Settings Changes — most recent first -->
            <div v-if="changes.length" class="section-card">
                <h2>Settings Changes</h2>
                <div class="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead class="whitespace-nowrap">Date</TableHead>
                                <TableHead class="whitespace-nowrap">Device</TableHead>
                                <TableHead class="whitespace-nowrap">Setting</TableHead>
                                <TableHead class="whitespace-nowrap">Change</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow v-for="change in changes" :key="changeKey(change)">
                                <TableCell class="whitespace-nowrap">{{
                                    formatDateFull(change.date)
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    change.device_name
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    settingLabel(change.key)
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">
                                    <span class="text-muted-foreground">{{
                                        change.old_value != null
                                            ? formatSettingValue(change.key, change.old_value)
                                            : '—'
                                    }}</span>
                                    <span class="mx-1">→</span>
                                    <span>{{
                                        change.new_value != null
                                            ? formatSettingValue(change.key, change.new_value)
                                            : '—'
                                    }}</span>
                                </TableCell>
                            </TableRow>
                        </TableBody>
                    </Table>
                </div>
            </div>
        </template>

        <div v-else class="no-data"><Info class="h-4 w-4" /> No prescription data available.</div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Loader2, Info } from '@lucide/vue'
import { Badge } from '@/components/ui/badge'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { getRxHistory, getRxCurrent, getRxCompare, getRxChanges } from '@/api/rx'
import { useApiLoad } from '@/composables/useApiLoad'
import { formatDateFull } from '@/utils/formatting'
import { settingLabel, formatSettingValue } from '@/utils/deviceSettings'
import type { RxPeriodResponse, RxSettingChange } from '@/types'
import ErrorState from '@/components/ErrorState.vue'

const { data, loading, error, reload } = useApiLoad(async () => {
    const [history, current, comparison, changesResponse] = await Promise.all([
        getRxHistory(),
        getRxCurrent(),
        getRxCompare(),
        getRxChanges(),
    ])
    return { history, current, comparison, changesResponse }
}, 'Failed to load RX data')

const history = computed(() => data.value?.history ?? [])
const current = computed(() => data.value?.current ?? null)
const comparison = computed(() => data.value?.comparison ?? null)
// Most recent first — users care about recent changes.
const changes = computed<RxSettingChange[]>(() =>
    [...(data.value?.changesResponse?.changes ?? [])].reverse(),
)

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
    const priorityKeys = [
        'mode',
        'pressure_fixed',
        'pressure_min',
        'pressure_max',
        'ipap',
        'epap',
        'ps',
        'epr_level',
    ]
    const parts: string[] = []
    for (const k of priorityKeys) {
        if (k in settings) {
            parts.push(`${settingLabel(k)}: ${formatSettingValue(k, settings[k])}`)
            if (parts.length === 4) break
        }
    }
    if (!parts.length) {
        return Object.entries(settings)
            .slice(0, 3)
            .map(([k, v]) => `${settingLabel(k)}: ${v}`)
            .join(', ')
    }
    return parts.join(', ')
}

function changeKey(change: RxSettingChange): string {
    return `${change.date}-${change.device_id}-${change.key}`
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
