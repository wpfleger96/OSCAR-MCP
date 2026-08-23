// @vitest-environment node
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import type { ValidationRunStatus, ValidatorType } from '@/types'

const { listMock, createMock, getMock, deleteMock } = vi.hoisted(() => ({
    listMock: vi.fn(),
    createMock: vi.fn(),
    getMock: vi.fn(),
    deleteMock: vi.fn(),
}))

vi.mock('@/api/validationRuns', () => ({
    listValidationRuns: listMock,
    createValidationRun: createMock,
    getValidationRun: getMock,
    deleteValidationRun: deleteMock,
}))

function makeRun(overrides: Partial<ValidationRunStatus> = {}): ValidationRunStatus {
    return {
        run_id: 1,
        validator_type: 'rera' as ValidatorType,
        state: 'running',
        reused: false,
        date_from: '2026-01-01',
        date_to: '2026-01-07',
        created_at: 1000,
        started_at: 1000,
        finished_at: null,
        job_id: 'job-1',
        error_message: null,
        owner_user_id: 1,
        engine_identity: {},
        validator_params: {},
        ...overrides,
    } as ValidationRunStatus
}

// The composable is a module-level singleton, so each test re-imports it fresh to
// reset the shared runs list and poll timer.
async function freshStore() {
    vi.resetModules()
    const mod = await import('@/composables/useValidationRuns')
    return mod.useValidationRuns()
}

beforeEach(() => {
    vi.useFakeTimers()
    listMock.mockReset()
    createMock.mockReset()
    getMock.mockReset()
    deleteMock.mockReset()
})

afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
})

describe('useValidationRuns', () => {
    it('test_enqueue_prepends_returned_run_to_shared_list', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({ runs: [], limit: 200, offset: 0, total: 0 })
        await store.refresh()

        createMock.mockResolvedValueOnce(makeRun({ run_id: 5, state: 'queued' }))
        const status = await store.enqueue({
            validator_type: 'rera',
            from_date: '2026-01-01',
            to_date: '2026-01-07',
        })

        expect(status.run_id).toBe(5)
        expect(store.runs.value[0].run_id).toBe(5)
    })

    it('test_enqueue_replaces_existing_run_in_place', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({
            runs: [makeRun({ run_id: 7, state: 'queued' })],
            limit: 200,
            offset: 0,
            total: 1,
        })
        await store.refresh()

        createMock.mockResolvedValueOnce(makeRun({ run_id: 7, state: 'running' }))
        await store.enqueue({ validator_type: 'rera', from_date: 'a', to_date: 'b' })

        expect(store.runs.value).toHaveLength(1)
        expect(store.runs.value[0].state).toBe('running')
    })

    it('test_polling_runs_while_active_then_stops_when_terminal', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({
            runs: [makeRun({ run_id: 1, state: 'running' })],
            limit: 200,
            offset: 0,
            total: 1,
        })
        await store.refresh()
        expect(listMock).toHaveBeenCalledTimes(1)

        // The scheduled poll observes the run reaching a terminal state.
        listMock.mockResolvedValueOnce({
            runs: [makeRun({ run_id: 1, state: 'succeeded' })],
            limit: 200,
            offset: 0,
            total: 1,
        })
        await vi.advanceTimersByTimeAsync(3000)
        expect(listMock).toHaveBeenCalledTimes(2)

        // No active runs remain, so no further poll is scheduled.
        await vi.advanceTimersByTimeAsync(6000)
        expect(listMock).toHaveBeenCalledTimes(2)
    })

    it('test_no_polling_scheduled_when_all_runs_terminal', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({
            runs: [makeRun({ run_id: 1, state: 'succeeded' })],
            limit: 200,
            offset: 0,
            total: 1,
        })
        await store.refresh()

        await vi.advanceTimersByTimeAsync(6000)
        expect(listMock).toHaveBeenCalledTimes(1)
    })

    it('test_concurrent_refresh_calls_dedupe_to_one_request', async () => {
        const store = await freshStore()
        let resolveList: (v: unknown) => void = () => {}
        listMock.mockReturnValueOnce(
            new Promise((r) => {
                resolveList = r
            }),
        )

        const p1 = store.refresh()
        const p2 = store.refresh() // guarded out while the first is in flight
        resolveList({ runs: [], limit: 200, offset: 0, total: 0 })
        await Promise.all([p1, p2])

        expect(listMock).toHaveBeenCalledTimes(1)
    })

    it('test_remove_drops_run_from_shared_list', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({
            runs: [makeRun({ run_id: 3, state: 'succeeded' })],
            limit: 200,
            offset: 0,
            total: 1,
        })
        await store.refresh()

        deleteMock.mockResolvedValueOnce(undefined)
        await store.remove(3)

        expect(store.runs.value.find((r) => r.run_id === 3)).toBeUndefined()
    })

    it('test_is_active_and_runs_for_type', async () => {
        const store = await freshStore()
        listMock.mockResolvedValueOnce({
            runs: [
                makeRun({ run_id: 1, validator_type: 'rera' as ValidatorType, state: 'running' }),
                makeRun({ run_id: 2, validator_type: 'fl' as ValidatorType, state: 'succeeded' }),
                makeRun({ run_id: 3, validator_type: 'rera' as ValidatorType, state: 'queued' }),
            ],
            limit: 200,
            offset: 0,
            total: 3,
        })
        await store.refresh()

        expect(store.runsForType('rera').map((r) => r.run_id)).toEqual([1, 3])
        expect(store.isActive(makeRun({ state: 'queued' }))).toBe(true)
        expect(store.isActive(makeRun({ state: 'running' }))).toBe(true)
        expect(store.isActive(makeRun({ state: 'succeeded' }))).toBe(false)
    })
})
