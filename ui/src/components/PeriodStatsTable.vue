<template>
    <div class="relative">
        <div
            v-if="loading"
            class="absolute inset-0 z-10 flex items-center justify-center bg-background/60"
        >
            <Loader2 class="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead>Period</TableHead>
                    <TableHead class="w-[90px]">Days Used</TableHead>
                    <TableHead class="w-[90px]">Avg Hours</TableHead>
                    <TableHead class="w-[90px]">Avg AHI</TableHead>
                    <TableHead class="w-[100px]">Median AHI</TableHead>
                    <TableHead class="w-[110px]">Avg Pressure</TableHead>
                    <TableHead class="w-[90px]">Avg Leak</TableHead>
                    <TableHead class="w-[90px]">Avg SpO₂</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                <template v-if="periods.length">
                    <TableRow
                        v-for="row in periods"
                        :key="row.period_start"
                        class="even:bg-muted/50"
                    >
                        <TableCell>
                            <template v-if="row.period_start === row.period_end">
                                {{ formatDateMonthDay(row.period_start) }}
                            </template>
                            <template v-else>
                                {{ formatDateMonthDay(row.period_start) }} –
                                {{ formatDateMonthDay(row.period_end) }}
                            </template>
                        </TableCell>
                        <TableCell>{{ row.days_used }}</TableCell>
                        <TableCell>{{ row.avg_hours_per_day?.toFixed(1) ?? '---' }}</TableCell>
                        <TableCell>
                            <span :class="ahiClass(row.avg_ahi)">
                                {{ row.avg_ahi?.toFixed(1) ?? '---' }}
                            </span>
                        </TableCell>
                        <TableCell>{{ row.median_ahi?.toFixed(1) ?? '---' }}</TableCell>
                        <TableCell>{{ row.avg_pressure?.toFixed(1) ?? '---' }}</TableCell>
                        <TableCell>{{ row.avg_leak?.toFixed(1) ?? '---' }}</TableCell>
                        <TableCell>{{ row.avg_spo2?.toFixed(1) ?? '---' }}</TableCell>
                    </TableRow>
                </template>
                <TableRow v-else>
                    <TableCell :colspan="8" class="h-24 text-center text-muted-foreground">
                        {{ emptyMessage ?? 'No period data available.' }}
                    </TableCell>
                </TableRow>
            </TableBody>
        </Table>
    </div>
</template>

<script setup lang="ts">
import { Loader2 } from '@lucide/vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import type { PeriodStatistics } from '@/types'
import { ahiClass, formatDateMonthDay } from '@/utils/formatting'

defineProps<{
    periods: PeriodStatistics[]
    loading: boolean
    emptyMessage?: string
}>()
</script>
