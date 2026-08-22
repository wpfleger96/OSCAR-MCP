<template>
    <div class="space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
            <h2 class="text-lg font-semibold">Run History</h2>
            <div class="flex items-center gap-2">
                <Select v-model="typeFilter">
                    <SelectTrigger class="w-[160px]">
                        <SelectValue placeholder="All validators" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All validators</SelectItem>
                        <SelectItem v-for="t in VALIDATOR_TYPES" :key="t" :value="t">
                            {{ VALIDATOR_LABELS[t] }}
                        </SelectItem>
                    </SelectContent>
                </Select>
                <Button variant="outline" size="sm" :disabled="loading" @click="refresh">
                    <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />
                </Button>
            </div>
        </div>

        <div class="rounded-md border overflow-x-auto">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Validator</TableHead>
                        <TableHead>Date Range</TableHead>
                        <TableHead>Engine</TableHead>
                        <TableHead>Params</TableHead>
                        <TableHead>State</TableHead>
                        <TableHead class="whitespace-nowrap">Created</TableHead>
                        <TableHead class="whitespace-nowrap">Finished</TableHead>
                        <TableHead></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    <TableRow v-if="visibleRuns.length === 0">
                        <TableCell :colspan="8" class="py-8 text-center text-muted-foreground">
                            No validation runs yet.
                        </TableCell>
                    </TableRow>
                    <TableRow
                        v-for="run in visibleRuns"
                        v-else
                        :key="run.run_id"
                        class="cursor-pointer even:bg-muted/50 hover:bg-muted"
                        @click="onSelect(run)"
                    >
                        <TableCell class="font-medium">
                            {{ VALIDATOR_LABELS[run.validator_type as ValidatorType] }}
                        </TableCell>
                        <TableCell class="whitespace-nowrap">
                            {{ run.date_from }} → {{ run.date_to }}
                        </TableCell>
                        <TableCell>
                            <div class="flex flex-wrap gap-1">
                                <Badge
                                    v-for="chip in identityChips(run.engine_identity)"
                                    :key="chip"
                                    variant="secondary"
                                >
                                    {{ chip }}
                                </Badge>
                            </div>
                        </TableCell>
                        <TableCell>
                            <div class="flex flex-wrap gap-1">
                                <Badge
                                    v-for="chip in identityChips(run.validator_params)"
                                    :key="chip"
                                    variant="outline"
                                >
                                    {{ chip }}
                                </Badge>
                                <span
                                    v-if="identityChips(run.validator_params).length === 0"
                                    class="text-xs text-muted-foreground"
                                    >—</span
                                >
                            </div>
                        </TableCell>
                        <TableCell>
                            <Badge :variant="stateVariant(run.state)">{{ run.state }}</Badge>
                            <Badge v-if="run.reused" variant="ghost" class="ml-1">reused</Badge>
                        </TableCell>
                        <TableCell class="whitespace-nowrap text-muted-foreground">
                            {{ formatRelativeTime(run.created_at) }}
                        </TableCell>
                        <TableCell class="whitespace-nowrap text-muted-foreground">
                            {{ run.finished_at ? formatRelativeTime(run.finished_at) : '—' }}
                        </TableCell>
                        <TableCell>
                            <Button
                                variant="ghost"
                                size="icon"
                                title="Delete run"
                                class="text-destructive hover:text-destructive"
                                @click.stop="confirmDelete(run)"
                            >
                                <Trash2 class="h-4 w-4" />
                            </Button>
                        </TableCell>
                    </TableRow>
                </TableBody>
            </Table>
        </div>

        <AlertDialog
            :open="deleteTarget !== null"
            @update:open="(v) => !v && (deleteTarget = null)"
        >
            <AlertDialogContent class="max-w-[420px]">
                <AlertDialogHeader>
                    <AlertDialogTitle>Delete validation run</AlertDialogTitle>
                    <AlertDialogDescription>
                        This permanently removes the stored run and its report. A running run is
                        cancelled instead.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <Button variant="destructive" :disabled="deleting" @click="executeDelete">
                        <Loader2 v-if="deleting" class="mr-2 h-4 w-4 animate-spin" />
                        Delete
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { RefreshCw, Trash2, Loader2 } from '@lucide/vue'
import { formatRelativeTime } from '@/utils/formatting'
import { identityChips, VALIDATOR_LABELS } from '@/utils/validationMetrics'
import { useValidationRuns } from '@/composables/useValidationRuns'
import type { BadgeVariants } from '@/components/ui/badge'
import type { ValidatorType, ValidationRunStatus } from '@/types'

const emit = defineEmits<{ select: [run: ValidationRunStatus] }>()

const store = useValidationRuns()
const { runs, loading, refresh, remove } = store

const VALIDATOR_TYPES = Object.keys(VALIDATOR_LABELS) as ValidatorType[]
const typeFilter = ref<'all' | ValidatorType>('all')

const visibleRuns = computed(() =>
    typeFilter.value === 'all'
        ? runs.value
        : runs.value.filter((r) => r.validator_type === typeFilter.value),
)

function stateVariant(state: string): BadgeVariants['variant'] {
    if (state === 'succeeded') return 'default'
    if (state === 'failed') return 'destructive'
    if (state === 'running' || state === 'queued') return 'secondary'
    return 'outline'
}

function onSelect(run: ValidationRunStatus): void {
    if (run.state === 'succeeded') emit('select', run)
}

const deleteTarget = ref<ValidationRunStatus | null>(null)
const deleting = ref(false)

function confirmDelete(run: ValidationRunStatus): void {
    deleteTarget.value = run
}

async function executeDelete(): Promise<void> {
    if (!deleteTarget.value) return
    deleting.value = true
    try {
        await remove(deleteTarget.value.run_id)
        deleteTarget.value = null
    } catch {
        void refresh()
    } finally {
        deleting.value = false
    }
}
</script>
