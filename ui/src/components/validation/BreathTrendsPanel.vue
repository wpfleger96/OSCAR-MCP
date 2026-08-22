<template>
    <ValidationPanelShell
        validator-type="breaths"
        :load-run-id="loadRunId"
        @update:report="rawReport = $event"
        @download-json="onDownloadJson"
        @download-csv="onDownloadCsv"
    >
        <template #default>
            <div v-if="report" class="space-y-6">
                <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                    <StatCard
                        v-for="ch in CHANNELS"
                        :key="ch.key"
                        :label="`${ch.label} Spearman`"
                        :value="report.aggregate[ch.key].mean_spearman_r"
                        :decimals="3"
                        glossary-key="spearman_r"
                    />
                    <StatCard
                        label="Sessions Compared"
                        :value="report.aggregate.sessions_compared"
                        :decimals="0"
                    />
                </div>

                <div class="rounded-md border overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Date</TableHead>
                                <TableHead>Breaths</TableHead>
                                <TableHead
                                    v-for="ch in CHANNELS"
                                    :key="ch.key"
                                    class="whitespace-nowrap"
                                >
                                    {{ ch.label }} <InfoHint glossary-key="spearman_r" />
                                </TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow v-if="report.sessions.length === 0">
                                <TableCell
                                    :colspan="CHANNELS.length + 2"
                                    class="py-8 text-center text-muted-foreground"
                                >
                                    No sessions in range.
                                </TableCell>
                            </TableRow>
                            <TableRow
                                v-for="s in report.sessions"
                                v-else
                                :key="s.session_id"
                                class="even:bg-muted/50"
                            >
                                <TableCell>
                                    <RouterLink
                                        :to="`/sessions/${s.session_id}/analysis`"
                                        class="text-primary hover:underline"
                                    >
                                        {{ formatDateShort(s.date) }}
                                    </RouterLink>
                                    <span
                                        v-if="s.skipped_reason"
                                        class="ml-1 text-xs text-muted-foreground"
                                        :title="nullReasonLabel(s.skipped_reason) ?? undefined"
                                        >(skipped)</span
                                    >
                                </TableCell>
                                <TableCell>{{ s.n_breaths }}</TableCell>
                                <TableCell v-for="ch in CHANNELS" :key="ch.key">
                                    {{ channelSpearman(s, ch.key) }}
                                </TableCell>
                            </TableRow>
                        </TableBody>
                    </Table>
                </div>
            </div>
        </template>
    </ValidationPanelShell>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatCard from '@/components/StatCard.vue'
import InfoHint from '@/components/InfoHint.vue'
import ValidationPanelShell from '@/components/validation/ValidationPanelShell.vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { formatDateShort, nullReasonLabel } from '@/utils/formatting'
import { downloadJson, downloadCsv } from '@/utils/download'
import type { BreathTrendsValidationReport, BreathTrendsSessionValidation } from '@/types'

defineProps<{ loadRunId?: number | null }>()

type ChannelKey = 'rr' | 'tv' | 'ti' | 'ie_ratio'
const CHANNELS: { key: ChannelKey; label: string }[] = [
    { key: 'rr', label: 'RR' },
    { key: 'tv', label: 'TV' },
    { key: 'ti', label: 'Ti' },
    { key: 'ie_ratio', label: 'I:E' },
]

const rawReport = ref<Record<string, unknown> | null>(null)
const report = computed(() => rawReport.value as unknown as BreathTrendsValidationReport | null)

function channelSpearman(s: BreathTrendsSessionValidation, key: ChannelKey): string {
    const ch = s.channels?.[key]
    if (!ch || ch.spearman_r == null) return '—'
    return ch.spearman_r.toFixed(3)
}

function fileStem(): string {
    const r = report.value
    return r ? `breaths-validation-${r.date_range_start}-${r.date_range_end}` : 'breaths-validation'
}

function onDownloadJson(): void {
    if (report.value) downloadJson(report.value, `${fileStem()}.json`)
}

function onDownloadCsv(): void {
    const r = report.value
    if (!r) return
    const headers = [
        'session_id',
        'date',
        'duration_hours',
        'skipped_reason',
        'n_breaths',
        'channel',
        'n_pairs',
        'spearman_r',
        'median_abs_error',
        'mean_bias',
        'channel_skipped_reason',
    ]
    const cell = (v: number | null | undefined): string => (v != null ? v.toFixed(4) : '')
    const rows: unknown[][] = []
    for (const s of r.sessions) {
        for (const { key } of CHANNELS) {
            const ch = s.channels?.[key]
            rows.push([
                s.session_id,
                s.date,
                s.duration_hours.toFixed(1),
                s.skipped_reason ?? '',
                s.n_breaths,
                key,
                ch?.n_pairs ?? '',
                cell(ch?.spearman_r),
                cell(ch?.median_abs_error),
                cell(ch?.mean_bias),
                ch?.skipped_reason ?? '',
            ])
        }
    }
    downloadCsv(headers, rows, `${fileStem()}.csv`)
}
</script>
