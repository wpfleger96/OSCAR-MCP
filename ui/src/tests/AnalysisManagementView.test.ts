import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'

vi.mock('@/composables/useAuth')
vi.mock('@/utils/formatting', () => ({ formatDateShort: (d: string) => d }))
vi.mock('@/components/PaginationBar.vue', () => ({
    default: { template: '<div />', props: ['offset', 'pageSize', 'total'] },
}))
vi.mock('@/components/DeleteConfirmDialog.vue', () => ({
    default: {
        template: '<div />',
        props: ['visible', 'title', 'message', 'loading', 'deleting'],
    },
}))
vi.mock('@/components/AnalysisJobsBanner.vue', () => ({
    default: { template: '<div />', props: ['jobs'] },
}))
vi.mock('@/api/analysis', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/api/analysis')>()
    return {
        ...actual,
        getAnalysisSessions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
        getAnalysisJobs: vi.fn().mockResolvedValue({ jobs: [] }),
        runBatchAnalysis: vi.fn().mockResolvedValue({ job_id: 'job-1', session_count: 5 }),
        deleteAnalysis: vi.fn(),
        getAnalysisDeletePreview: vi.fn(),
        cancelAnalysisJob: vi.fn(),
    }
})

import AnalysisManagementView from '@/views/AnalysisManagementView.vue'
import { useAuth } from '@/composables/useAuth'
import { runBatchAnalysis } from '@/api/analysis'
import { makeAuthMock } from './helpers/mockUseAuth'

describe('AnalysisManagementView — analyze missing', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(useAuth).mockReturnValue(
            makeAuthMock({ canWrite: ref(true), isAuthenticated: ref(true) }) as never,
        )
        vi.mocked(runBatchAnalysis).mockResolvedValue({ job_id: 'job-1', session_count: 5 })
    })

    async function mountView() {
        const wrapper = mount(AnalysisManagementView)
        await flushPromises()
        return wrapper
    }

    it('test_handleAnalyzeMissing_sends_missing_only_without_dates', async () => {
        // Falsifiable: remove missing_only from the payload in handleAnalyzeMissing;
        // objectContaining({ missing_only: true }) fails.
        const wrapper = await mountView()
        const vm = wrapper.vm as unknown as { handleAnalyzeMissing: () => Promise<void> }

        await vm.handleAnalyzeMissing()
        await flushPromises()

        expect(runBatchAnalysis).toHaveBeenCalledOnce()
        const [body] = vi.mocked(runBatchAnalysis).mock.calls[0]
        expect(body).toMatchObject({ missing_only: true, store_results: true })
        expect(body).not.toHaveProperty('from_date')
        expect(body).not.toHaveProperty('to_date')
    })

    it('test_handleBatchRun_does_not_include_missing_only', async () => {
        // Guard: the existing batch-run path must not gain missing_only.
        // batchFrom/batchTo start as empty strings; handleBatchRun proceeds without
        // the date-validation check (both falsy) and fires runBatchAnalysis.
        const wrapper = await mountView()
        const vm = wrapper.vm as unknown as { handleBatchRun: () => Promise<void> }

        await vm.handleBatchRun()
        await flushPromises()

        expect(runBatchAnalysis).toHaveBeenCalledOnce()
        const [body] = vi.mocked(runBatchAnalysis).mock.calls[0]
        expect(body).not.toHaveProperty('missing_only')
        expect(body).toMatchObject({ store_results: true })
    })
})
