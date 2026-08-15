<template>
    <div class="mx-auto px-4 py-6" style="max-width: 1200px">
        <h1 class="text-2xl font-bold mb-6">Validation</h1>

        <div v-if="!result" class="space-y-4 max-w-md">
            <div class="space-y-2">
                <label class="text-sm font-medium">From Date</label>
                <DatePickerInput
                    v-model="fromDate"
                    :is-date-disabled="isDateDisabled"
                    :min-value="minValue"
                    :max-value="maxValue"
                />
            </div>
            <div class="space-y-2">
                <label class="text-sm font-medium">To Date</label>
                <DatePickerInput
                    v-model="toDate"
                    :is-date-disabled="isDateDisabled"
                    :min-value="minValue"
                    :max-value="maxValue"
                />
            </div>
            <div class="space-y-2">
                <label class="text-sm font-medium">Mode</label>
                <Select v-model="mode">
                    <SelectTrigger class="w-full">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="aasm">aasm</SelectItem>
                        <SelectItem value="aasm_relaxed">aasm_relaxed</SelectItem>
                        <SelectItem value="resmed">resmed</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <div v-if="error" class="flex items-center gap-2 text-sm text-destructive">
                <AlertTriangle class="h-4 w-4" />
                {{ error }}
            </div>

            <Button :disabled="!canWrite || !fromDate || !toDate || running" @click="handleRun">
                <Loader2 v-if="running" class="mr-2 h-4 w-4 animate-spin" />
                Run Validation
            </Button>
        </div>

        <div v-else class="space-y-6">
            <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                <StatCard
                    label="Avg Apnea Sensitivity"
                    :value="
                        result.aggregate.avg_apnea_sensitivity != null
                            ? result.aggregate.avg_apnea_sensitivity * 100
                            : null
                    "
                    unit="%"
                    :decimals="1"
                    glossary-key="sensitivity"
                />
                <StatCard
                    label="Avg Apnea F1"
                    :value="
                        result.aggregate.avg_apnea_f1 != null
                            ? result.aggregate.avg_apnea_f1 * 100
                            : null
                    "
                    unit="%"
                    :decimals="1"
                    glossary-key="f1"
                />
                <StatCard
                    label="Avg Hypopnea Sensitivity"
                    :value="
                        result.aggregate.avg_hypopnea_sensitivity != null
                            ? result.aggregate.avg_hypopnea_sensitivity * 100
                            : null
                    "
                    unit="%"
                    :decimals="1"
                    glossary-key="sensitivity"
                />
                <StatCard
                    label="Avg Hypopnea F1"
                    :value="
                        result.aggregate.avg_hypopnea_f1 != null
                            ? result.aggregate.avg_hypopnea_f1 * 100
                            : null
                    "
                    unit="%"
                    :decimals="1"
                    glossary-key="f1"
                />
                <StatCard
                    label="Sessions Validated"
                    :value="result.aggregate.total_sessions"
                    :decimals="0"
                />
            </div>

            <!-- Deliberate mobile treatment: this dense validation metrics matrix stays a horizontally scrolling table rather than cards. -->
            <div class="rounded-md border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Date</TableHead>
                            <TableHead>Duration</TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Apnea Sens <InfoHint glossary-key="sensitivity"
                            /></TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Apnea Prec <InfoHint glossary-key="precision"
                            /></TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Apnea F1 <InfoHint glossary-key="f1"
                            /></TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Hypopnea Sens <InfoHint glossary-key="sensitivity"
                            /></TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Hypopnea Prec <InfoHint glossary-key="precision"
                            /></TableHead>
                            <TableHead class="whitespace-nowrap"
                                >Hypopnea F1 <InfoHint glossary-key="f1"
                            /></TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        <TableRow v-if="result.sessions.length === 0">
                            <TableCell :colspan="8" class="py-8 text-center text-muted-foreground">
                                No validated sessions found in the selected date range.
                            </TableCell>
                        </TableRow>
                        <TableRow
                            v-else
                            v-for="session in result.sessions"
                            :key="session.session_id"
                            :class="{
                                'bg-amber-50 dark:bg-amber-950/30': isLowSensitivity(session),
                                'even:bg-muted/50': !isLowSensitivity(session),
                            }"
                        >
                            <TableCell>
                                <RouterLink
                                    :to="`/sessions/${session.session_id}/analysis`"
                                    class="text-primary hover:underline"
                                >
                                    {{ formatDateShort(session.date) }}
                                </RouterLink>
                            </TableCell>
                            <TableCell>{{ session.duration_hours.toFixed(1) }}h</TableCell>
                            <TableCell>{{ pct(session.apnea_sensitivity) }}</TableCell>
                            <TableCell>{{ pct(session.apnea_precision) }}</TableCell>
                            <TableCell>{{ pct(session.apnea_f1) }}</TableCell>
                            <TableCell>{{ pct(session.hypopnea_sensitivity) }}</TableCell>
                            <TableCell>{{ pct(session.hypopnea_precision) }}</TableCell>
                            <TableCell>{{ pct(session.hypopnea_f1) }}</TableCell>
                        </TableRow>
                    </TableBody>
                </Table>
            </div>

            <div class="flex gap-2 flex-wrap">
                <Button variant="outline" @click="result = null">Run Again</Button>
                <Button variant="outline" @click="downloadJson">Download JSON</Button>
                <Button variant="outline" @click="downloadCsv">Download CSV</Button>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import StatCard from '@/components/StatCard.vue'
import InfoHint from '@/components/InfoHint.vue'
import { Button } from '@/components/ui/button'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Loader2, AlertTriangle } from '@lucide/vue'
import { formatDateShort } from '@/utils/formatting'
import { runValidation } from '@/api/validation'
import { useAuth } from '@/composables/useAuth'
import { useAvailableDates } from '@/composables/useAvailableDates'
import DatePickerInput from '@/components/DatePickerInput.vue'
import type { ValidationReport, SessionValidation } from '@/types'

const { canWrite } = useAuth()
const { load: loadDates, isDateDisabled, minValue, maxValue } = useAvailableDates()

onMounted(() => {
    void loadDates()
})

const fromDate = ref('')
const toDate = ref('')
const mode = ref<'aasm' | 'aasm_relaxed' | 'resmed'>('aasm')
const running = ref(false)
const result = ref<ValidationReport | null>(null)
const error = ref<string | null>(null)

function pct(value: number | null | undefined): string {
    return value != null ? `${(value * 100).toFixed(1)}%` : '---'
}

function csvCell(value: unknown): string {
    const s = String(value ?? '')
    // Neutralize formula injection: prefix cells starting with =, +, -, or @
    const safe = /^[=+\-@]/.test(s) ? `'${s}` : s
    // Wrap in double quotes, escape embedded double quotes as ""
    return `"${safe.replaceAll('"', '""')}"`
}

function isLowSensitivity(session: SessionValidation): boolean {
    return session.apnea_sensitivity < 0.7 || session.hypopnea_sensitivity < 0.7
}

function downloadBlob(content: string, filename: string, mimeType: string): void {
    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
}

function downloadJson(): void {
    if (!result.value) return
    const filename = `validation-report-${fromDate.value}-${toDate.value}.json`
    downloadBlob(JSON.stringify(result.value, null, 2), filename, 'application/json')
}

function downloadCsv(): void {
    if (!result.value) return
    const headers = [
        'session_id',
        'date',
        'duration_hours',
        'machine_events',
        'programmatic_events',
        'apnea_sens',
        'apnea_prec',
        'apnea_f1',
        'hypopnea_sens',
        'hypopnea_prec',
        'hypopnea_f1',
        'notes',
    ]
    const rows = result.value.sessions.map((s) => [
        s.session_id,
        s.date,
        s.duration_hours.toFixed(1),
        s.machine_event_count,
        s.programmatic_event_count,
        `${(s.apnea_sensitivity * 100).toFixed(0)}%`,
        `${(s.apnea_precision * 100).toFixed(0)}%`,
        s.apnea_f1.toFixed(2),
        `${(s.hypopnea_sensitivity * 100).toFixed(0)}%`,
        `${(s.hypopnea_precision * 100).toFixed(0)}%`,
        s.hypopnea_f1.toFixed(2),
        s.notes ?? '',
    ])
    const csv = [headers, ...rows].map((r) => r.map(csvCell).join(',')).join('\n')
    const filename = `validation-report-${fromDate.value}-${toDate.value}.csv`
    downloadBlob(csv, filename, 'text/csv')
}

async function handleRun(): Promise<void> {
    if (fromDate.value && toDate.value && fromDate.value > toDate.value) {
        error.value = 'From date must be before To date'
        return
    }
    running.value = true
    error.value = null
    try {
        result.value = await runValidation({
            from_date: fromDate.value,
            to_date: toDate.value,
            mode: mode.value,
        })
    } catch (err) {
        error.value = err instanceof Error ? err.message : 'Validation failed'
    } finally {
        running.value = false
    }
}
</script>
