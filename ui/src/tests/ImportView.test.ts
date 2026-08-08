import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref, defineComponent } from 'vue'
import type { PipelineJobStatus } from '@/types'

vi.mock('@/composables/useAuth')
vi.mock('@/api/importJobs', () => ({
    ACTIVE_PIPELINE_STAGES: new Set([
        'uploading',
        'queued',
        'importing',
        'analysis_queued',
        'analyzing',
    ]),
    getImportJobs: vi.fn(),
    cancelImport: vi.fn(),
}))
vi.mock('@/api/analysis', () => ({
    cancelAnalysisJob: vi.fn(),
}))
vi.mock('@/api/import', () => ({
    importFiles: vi.fn(),
}))
vi.mock('@/utils/formatting', () => ({
    formatBytes: (n: number) => `${n}B`,
}))
vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

// Stub ImportJobsPanel to emit cancel with jobs[0] on button click.
vi.mock('@/components/ImportJobsPanel.vue', () => ({
    default: defineComponent({
        name: 'ImportJobsPanel',
        props: ['jobs'],
        emits: ['cancel'],
        template: `<div class="jobs-panel"><button v-if="jobs.length" class="cancel-trigger" @click="$emit('cancel', jobs[0])">Cancel</button></div>`,
    }),
}))

import { makeAuthMock } from './helpers/mockUseAuth'
import { useAuth } from '@/composables/useAuth'
import { getImportJobs, cancelImport } from '@/api/importJobs'
import { cancelAnalysisJob } from '@/api/analysis'
import ImportView from '@/views/ImportView.vue'

function makeJob(overrides: Partial<PipelineJobStatus> = {}): PipelineJobStatus {
    return {
        job_id: 'job-1',
        job_type: 'upload',
        state: 'active',
        stage: 'queued',
        file_count: 3,
        created_at: '2024-01-01T00:00:00+00:00',
        finished_at: null,
        progress_message: null,
        sessions_imported: null,
        import_result: null,
        error_message: null,
        analysis_job_id: null,
        analysis_queued: null,
        linked_analysis: null,
        ...overrides,
    }
}

async function mountWithJob(job: PipelineJobStatus) {
    vi.mocked(getImportJobs).mockResolvedValue({ jobs: [job] })
    const wrapper = mount(ImportView)
    await flushPromises()
    return wrapper
}

describe('ImportView cancel handler', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.mocked(useAuth).mockReturnValue(
            makeAuthMock({
                profiles: ref([{ id: 1, name: 'Test' }]),
                activeProfileId: ref(1),
            }) as never,
        )
        vi.mocked(getImportJobs).mockResolvedValue({ jobs: [] })
        vi.mocked(cancelImport).mockResolvedValue(undefined)
        vi.mocked(cancelAnalysisJob).mockResolvedValue(undefined)
    })

    it('test_analyzing_stage_calls_cancelAnalysisJob_with_analysis_job_id', async () => {
        const job = makeJob({
            stage: 'analyzing',
            analysis_job_id: 'aj-123',
        })
        const wrapper = await mountWithJob(job)

        await wrapper.find('.cancel-trigger').trigger('click')
        await flushPromises()

        expect(cancelAnalysisJob).toHaveBeenCalledWith('aj-123')
        expect(cancelImport).not.toHaveBeenCalled()
    })

    it('test_analysis_queued_stage_calls_cancelAnalysisJob_with_analysis_job_id', async () => {
        const job = makeJob({
            stage: 'analysis_queued',
            analysis_job_id: 'aj-queued',
        })
        const wrapper = await mountWithJob(job)

        await wrapper.find('.cancel-trigger').trigger('click')
        await flushPromises()

        expect(cancelAnalysisJob).toHaveBeenCalledWith('aj-queued')
        expect(cancelImport).not.toHaveBeenCalled()
    })

    it('test_importing_stage_calls_cancelImport', async () => {
        const job = makeJob({ job_id: 'import-1', stage: 'importing' })
        const wrapper = await mountWithJob(job)

        await wrapper.find('.cancel-trigger').trigger('click')
        await flushPromises()

        expect(cancelImport).toHaveBeenCalledWith('import-1')
        expect(cancelAnalysisJob).not.toHaveBeenCalled()
    })

    it('test_analyzing_stage_with_null_analysis_job_id_falls_back_to_cancelImport', async () => {
        const job = makeJob({
            job_id: 'import-2',
            stage: 'analyzing',
            analysis_job_id: null,
        })
        const wrapper = await mountWithJob(job)

        await wrapper.find('.cancel-trigger').trigger('click')
        await flushPromises()

        expect(cancelImport).toHaveBeenCalledWith('import-2')
        expect(cancelAnalysisJob).not.toHaveBeenCalled()
    })
})
