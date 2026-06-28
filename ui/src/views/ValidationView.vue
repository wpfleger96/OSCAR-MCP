<template>
    <div class="mx-auto px-4 py-6" style="max-width: 1200px">
        <h1 class="text-2xl font-bold mb-6">Validation</h1>

        <div v-if="!result" class="space-y-4 max-w-md">
            <div class="space-y-2">
                <label class="text-sm font-medium">From Date</label>
                <input v-model="fromDate" type="date" required class="date-input" />
            </div>
            <div class="space-y-2">
                <label class="text-sm font-medium">To Date</label>
                <input v-model="toDate" type="date" required class="date-input" />
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

            <Button :disabled="!fromDate || !toDate || running" @click="handleRun">
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
                />
                <StatCard
                    label="Sessions Validated"
                    :value="result.aggregate.total_sessions"
                    :decimals="0"
                />
            </div>

            <div class="rounded-md border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Date</TableHead>
                            <TableHead>Duration</TableHead>
                            <TableHead>Apnea Sens</TableHead>
                            <TableHead>Apnea Prec</TableHead>
                            <TableHead>Apnea F1</TableHead>
                            <TableHead>Hypopnea Sens</TableHead>
                            <TableHead>Hypopnea Prec</TableHead>
                            <TableHead>Hypopnea F1</TableHead>
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

            <Button variant="outline" @click="result = null">Run Again</Button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink } from 'vue-router'
import StatCard from '@/components/StatCard.vue'
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
import type { ValidationReport, SessionValidation } from '@/types'

const fromDate = ref('')
const toDate = ref('')
const mode = ref<'aasm' | 'aasm_relaxed' | 'resmed'>('aasm')
const running = ref(false)
const result = ref<ValidationReport | null>(null)
const error = ref<string | null>(null)

function pct(value: number | null | undefined): string {
    return value != null ? `${(value * 100).toFixed(1)}%` : '---'
}

function isLowSensitivity(session: SessionValidation): boolean {
    return session.apnea_sensitivity < 0.7 || session.hypopnea_sensitivity < 0.7
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
