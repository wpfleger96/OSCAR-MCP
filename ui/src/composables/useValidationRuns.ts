import { ref } from 'vue'
import {
    createValidationRun,
    listValidationRuns,
    getValidationRun,
    deleteValidationRun,
    type CreateValidationRunBody,
} from '@/api/validationRuns'
import type { ValidatorType, ValidationRunStatus, ValidationRunDetail } from '@/types'

const ACTIVE_STATES = new Set(['queued', 'running'])

// Module-level singleton so the run history, per-tab panels, and the job banner
// all observe one shared list and one polling loop (mirrors useAvailableDates).
const runs = ref<ValidationRunStatus[]>([])
const loading = ref(false)
const listError = ref<string | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let refreshing = false

function isActive(run: ValidationRunStatus): boolean {
    return ACTIVE_STATES.has(run.state)
}

async function refresh(): Promise<void> {
    if (refreshing) return
    refreshing = true
    loading.value = true
    try {
        // The list merges in-memory jobs and DB rows; a generous page keeps the
        // full retained history visible without a second request.
        const res = await listValidationRuns({ limit: 200, offset: 0 })
        runs.value = res.runs
        listError.value = null
        // Landing on the page (or returning to it) with a queued/running job must
        // resume the poll loop, not wait for a manual refresh.
        ensurePolling()
    } catch (err) {
        listError.value = err instanceof Error ? err.message : 'Failed to load runs'
    } finally {
        loading.value = false
        refreshing = false
    }
}

function ensurePolling(): void {
    if (pollTimer !== null) return
    const anyActive = runs.value.some(isActive)
    if (!anyActive) return
    pollTimer = setTimeout(async () => {
        pollTimer = null
        await refresh()
        ensurePolling()
    }, 3000)
}

async function enqueue(body: CreateValidationRunBody): Promise<ValidationRunStatus> {
    const status = await createValidationRun(body)
    // Splice the returned run into the shared list immediately so the banner and
    // history reflect it before the next poll, then start/continue polling.
    const idx = runs.value.findIndex((r) => r.run_id === status.run_id)
    if (idx >= 0) runs.value.splice(idx, 1, status)
    else runs.value = [status, ...runs.value]
    ensurePolling()
    return status
}

async function getDetail(runId: number): Promise<ValidationRunDetail> {
    return getValidationRun(runId)
}

async function remove(runId: number): Promise<void> {
    await deleteValidationRun(runId)
    runs.value = runs.value.filter((r) => r.run_id !== runId)
}

function runsForType(type: ValidatorType): ValidationRunStatus[] {
    return runs.value.filter((r) => r.validator_type === type)
}

export function useValidationRuns() {
    return {
        runs,
        loading,
        listError,
        refresh,
        enqueue,
        getDetail,
        remove,
        runsForType,
        isActive,
    }
}
