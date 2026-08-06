import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { ref } from 'vue'
import type { PipelineJobStatus } from '@/types'

// Mock api.post so the real importFromPath runs but HTTP never fires.
vi.mock('@/api/client', () => ({
    default: { post: vi.fn() },
    apiPost: () => vi.fn(),
    apiGet: () => vi.fn(),
    apiPatch: () => vi.fn(),
    apiDelete: () => vi.fn(),
    apiGetOrNull: () => vi.fn(),
    createApiEndpoint: () => vi.fn(),
}))
vi.mock('@/composables/useAuth')
// Keep real importFromPath; mock only the functions not under test.
vi.mock('@/api/import', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/api/import')>()
    return { ...actual, detectSources: vi.fn(), importFiles: vi.fn() }
})
// Mock importJobs so polling never hits the network and the panel renders empty.
// ACTIVE_PIPELINE_STAGES must be included — the mock factory replaces the whole module.
vi.mock('@/api/importJobs', () => ({
    ACTIVE_PIPELINE_STAGES: new Set([
        'uploading',
        'queued',
        'importing',
        'analysis_queued',
        'analyzing',
    ]),
    getImportJobs: vi.fn().mockResolvedValue({ jobs: [] }),
    cancelImport: vi.fn().mockResolvedValue(undefined),
}))
// Stub ImportJobsPanel so tests don't depend on the parallel agent's file existing.
vi.mock('@/components/ImportJobsPanel.vue', () => ({
    default: { template: '<div />', props: ['jobs'], emits: ['cancel'] },
}))
vi.mock('@/composables/useDarkMode', () => ({
    useDarkMode: () => ({ isDark: ref(false), toggleDark: vi.fn() }),
}))
vi.mock('@/utils/formatting', () => ({ formatBytes: (n: number) => `${n}B` }))

import api from '@/api/client'
import { useAuth } from '@/composables/useAuth'
import { getImportJobs, cancelImport } from '@/api/importJobs'

// Minimal job factory for polling/cancel tests — only stage is load-bearing for poll logic.
function jobWith(stage: string): PipelineJobStatus {
    return {
        job_id: 'job-1',
        job_type: 'upload',
        state: 'active',
        stage,
        file_count: 1,
        created_at: 1000,
        finished_at: null,
        progress_message: null,
        sessions_imported: null,
        import_result: null,
        error_message: null,
        analysis_job_id: null,
        analysis_queued: null,
        linked_analysis: null,
    } as unknown as PipelineJobStatus
}

function makeAuthMock() {
    return {
        isAuthenticated: ref(true) as never,
        isLocal: ref(false) as never,
        fetchStatus: vi.fn().mockResolvedValue(undefined),
        refreshStatus: vi.fn().mockResolvedValue(undefined),
        user: ref({ id: 1, email: 'u@x.com', display_name: 'U', role: 'user' }) as never,
        profiles: ref([]) as never,
        activeProfileId: ref(1) as never,
        authMode: ref('multiuser') as never,
        profileKey: ref(0) as never,
        role: ref(null) as never,
        canWrite: ref(true) as never,
        demoAvailable: ref(false) as never,
        login: vi.fn(),
        demoLogin: vi.fn(),
        logout: vi.fn(),
        clearAuth: vi.fn(),
        setActiveProfile: vi.fn(),
    }
}

async function mountImportView() {
    const ImportView = (await import('@/views/ImportView.vue')).default
    const testRouter = createRouter({
        history: createWebHistory(),
        routes: [{ path: '/import', component: ImportView }],
    })
    await testRouter.push('/import')
    return mount(ImportView, { global: { plugins: [testRouter] } })
}

describe('wire contract: importFromPath sends profile_id in HTTP body', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(api.post).mockResolvedValue({ data: { job_id: 'job-1' } })
        vi.mocked(useAuth).mockReturnValue({
            isAuthenticated: ref(true) as never,
            isLocal: ref(false) as never,
            fetchStatus: vi.fn().mockResolvedValue(undefined),
            refreshStatus: vi.fn().mockResolvedValue(undefined),
            user: ref({ id: 1, email: 'u@x.com', display_name: 'U', role: 'user' }) as never,
            profiles: ref([
                { id: 1, name: 'Primary' },
                { id: 2, name: 'Work' },
            ]) as never,
            activeProfileId: ref(1) as never,
            authMode: ref('multiuser') as never,
            profileKey: ref(0) as never,
            role: ref(null) as never,
            canWrite: ref(true) as never,
            demoAvailable: ref(false) as never,
            login: vi.fn(),
            demoLogin: vi.fn(),
            logout: vi.fn(),
            clearAuth: vi.fn(),
            setActiveProfile: vi.fn(),
        })
    })

    it('test_handlePathImport_sends_profile_id_not_target_profile_id', async () => {
        // Falsifiable: rename profile_id → target_profile_id in api/import.ts line 26;
        // api.post receives { target_profile_id: 2 } and objectContaining({ profile_id: 2 }) fails.
        const ImportView = (await import('@/views/ImportView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/import', component: ImportView }],
        })
        await testRouter.push('/import')
        const wrapper = mount(ImportView, { global: { plugins: [testRouter] } })

        const vm = wrapper.vm as unknown as Record<string, unknown>
        // Pre-populate path-import state so the early-return guard passes
        vm['detectedSources'] = [{ parser_name: 'resmed', root_path: '/data/sd' }]
        vm['selectedSources'] = new Set([0])
        // Select profile 2 — different from active profile 1
        vm['selectedProfileId'] = 2

        await (vm as { handlePathImport: () => Promise<void> }).handlePathImport()

        expect(api.post).toHaveBeenCalledWith(
            '/import/path',
            expect.objectContaining({ profile_id: 2 }),
        )
    })

    it('test_handlePathImport_resets_to_idle_after_202', async () => {
        // After a successful 202, the path form must be freed immediately (pathPhase → idle)
        // so the user can start a new import without waiting for the background job.
        const ImportView = (await import('@/views/ImportView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/import', component: ImportView }],
        })
        await testRouter.push('/import')
        const wrapper = mount(ImportView, { global: { plugins: [testRouter] } })

        const vm = wrapper.vm as unknown as Record<string, unknown>
        vm['detectedSources'] = [{ parser_name: 'resmed', root_path: '/data/sd' }]
        vm['selectedSources'] = new Set([0])

        await (vm as { handlePathImport: () => Promise<void> }).handlePathImport()

        // Form freed: path phase back to idle, no sources left selected
        expect(vm['pathPhase']).toBe('idle')
        expect((vm['detectedSources'] as unknown[]).length).toBe(0)
    })

    it('test_handleImport_resets_to_idle_after_202', async () => {
        // After a successful 202 from the upload POST, the dropzone must be freed immediately
        // (uploadPhase → idle) so the user can queue another import without waiting.
        const { importFiles } = await import('@/api/import')
        vi.mocked(importFiles).mockResolvedValueOnce({ job_id: 'job-2' })

        const ImportView = (await import('@/views/ImportView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/import', component: ImportView }],
        })
        await testRouter.push('/import')
        const wrapper = mount(ImportView, { global: { plugins: [testRouter] } })

        const vm = wrapper.vm as unknown as Record<string, unknown>
        // Pre-populate file entries so handleImport proceeds past the early return
        vm['fileEntries'] = [{ file: new File(['x'], 'test.edf'), path: 'sd/test.edf' }]

        await (vm as { handleImport: () => Promise<void> }).handleImport()

        // Dropzone freed: phase back to idle, file list cleared
        expect(vm['uploadPhase']).toBe('idle')
        expect((vm['fileEntries'] as unknown[]).length).toBe(0)
    })
})

describe('import jobs polling behavior', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(useAuth).mockReturnValue(makeAuthMock())
    })

    it('test_poll_stops_when_all_jobs_terminal', async () => {
        vi.useFakeTimers()
        vi.mocked(getImportJobs).mockResolvedValue({ jobs: [jobWith('done')] })

        const wrapper = await mountImportView()
        await flushPromises()

        const callsBefore = vi.mocked(getImportJobs).mock.calls.length

        // Advance past two poll intervals; no new calls expected since job is terminal.
        await vi.advanceTimersByTimeAsync(7000)

        expect(vi.mocked(getImportJobs).mock.calls.length).toBe(callsBefore)

        wrapper.unmount()
        vi.useRealTimers()
    })

    it('test_poll_recovers_after_fetch_error', async () => {
        // Regression for fix 1: a transient network error must not freeze the poll.
        vi.useFakeTimers()
        vi.mocked(getImportJobs)
            .mockResolvedValueOnce({ jobs: [jobWith('importing')] }) // call 1 – active job
            .mockRejectedValueOnce(new Error('network error')) // call 2 – transient blip
            .mockResolvedValueOnce({ jobs: [] }) // call 3 – recovers

        const wrapper = await mountImportView()
        await flushPromises() // call 1 completes; poll timer scheduled

        await vi.advanceTimersByTimeAsync(3000) // timer fires → call 2 (rejects, reschedules)
        await vi.advanceTimersByTimeAsync(3000) // timer fires → call 3

        expect(vi.mocked(getImportJobs)).toHaveBeenCalledTimes(3)

        wrapper.unmount()
        vi.useRealTimers()
    })
})

describe('import job cancel handling', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(useAuth).mockReturnValue(makeAuthMock())
    })

    it('test_cancel_wiring_calls_cancelImport_with_job_id_and_refetches', async () => {
        vi.mocked(getImportJobs).mockResolvedValue({ jobs: [jobWith('importing')] })
        vi.mocked(cancelImport).mockResolvedValue(undefined)

        const wrapper = await mountImportView()
        await flushPromises()

        const callsBefore = vi.mocked(getImportJobs).mock.calls.length
        const vm = wrapper.vm as unknown as {
            handleCancelImportJob: (id: string) => Promise<void>
        }
        await vm.handleCancelImportJob('job-1')
        await flushPromises()

        expect(cancelImport).toHaveBeenCalledWith('job-1')
        expect(vi.mocked(getImportJobs).mock.calls.length).toBeGreaterThan(callsBefore)

        wrapper.unmount()
    })

    it('test_cancel_refetches_even_when_cancelImport_rejects', async () => {
        // Regression for fix 2: a 404/network error on cancel must not skip the refresh.
        vi.mocked(getImportJobs).mockResolvedValue({ jobs: [jobWith('importing')] })
        vi.mocked(cancelImport).mockRejectedValue(new Error('404 Not Found'))

        const wrapper = await mountImportView()
        await flushPromises()

        const callsBefore = vi.mocked(getImportJobs).mock.calls.length
        const vm = wrapper.vm as unknown as {
            handleCancelImportJob: (id: string) => Promise<void>
        }
        await vm.handleCancelImportJob('job-1')
        await flushPromises()

        expect(vi.mocked(getImportJobs).mock.calls.length).toBeGreaterThan(callsBefore)

        wrapper.unmount()
    })
})
