import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))
// ImportJobsPanel now imports ACTIVE_PIPELINE_STAGES from '@/api/importJobs'.
// The factory replaces the whole module, so the Set must be included here.
vi.mock('@/api/importJobs', () => ({
    ACTIVE_PIPELINE_STAGES: new Set([
        'uploading',
        'queued',
        'importing',
        'analysis_queued',
        'analyzing',
    ]),
}))

import ImportJobsPanel from '@/components/ImportJobsPanel.vue'
import type { PipelineJobStatus } from '@/types'

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

describe('ImportJobsPanel', () => {
    it('test_empty_jobs_renders_nothing', () => {
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [] } })
        expect(wrapper.find('.jobs-banner').exists()).toBe(false)
    })

    it('test_importing_row_shows_progress_message_and_cancel_button', () => {
        const job = makeJob({ stage: 'importing', progress_message: 'Processing file 2 of 3' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('Processing file 2 of 3')
        expect(wrapper.find('button[title="Cancel job"]').exists()).toBe(true)
    })

    it('test_analyzing_row_shows_linked_progress', () => {
        const job = makeJob({
            stage: 'analyzing',
            linked_analysis: {
                job_id: 'aj-1',
                state: 'running',
                progress_completed: 4,
                progress_total: 10,
                error_message: null,
            },
        })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('4/10 sessions')
    })

    it('test_failed_row_shows_error_message_and_no_cancel_button', () => {
        const job = makeJob({ stage: 'failed', error_message: 'Disk full' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('Disk full')
        expect(wrapper.find('button[title="Cancel job"]').exists()).toBe(false)
    })

    it('test_done_row_shows_imported_count_and_no_cancel_button', () => {
        const job = makeJob({ stage: 'done', sessions_imported: 7 })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('7 session(s) imported')
        expect(wrapper.find('button[title="Cancel job"]').exists()).toBe(false)
    })

    it('test_cancel_click_emits_cancel_with_job_object', async () => {
        const job = makeJob({ job_id: 'abc-123', stage: 'importing' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        await wrapper.find('button[title="Cancel job"]').trigger('click')

        expect(wrapper.emitted('cancel')).toEqual([[job]])
    })

    it('test_analyzing_row_cancel_emits_full_job_object', async () => {
        const job = makeJob({
            stage: 'analyzing',
            analysis_job_id: 'aj-99',
            linked_analysis: {
                job_id: 'aj-99',
                state: 'running',
                progress_completed: 2,
                progress_total: 5,
                error_message: null,
            },
        })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        await wrapper.find('button[title="Cancel job"]').trigger('click')

        expect(wrapper.emitted('cancel')).toEqual([[job]])
    })

    it('test_analysis_skipped_shows_warning_text', () => {
        const job = makeJob({ stage: 'analysis_skipped' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('Analysis skipped (queue full)')
    })

    it('test_import_result_summary_only_shown_for_done_stage', () => {
        // The import_result block is deliberately gated on stage === 'done'.
        // analysis_queued and analyzing must not render it even when populated.
        const mockResult = {
            total_imported: 5,
            total_skipped: 1,
            total_failed: 0,
            sources: [],
            warnings: [],
        }

        for (const stage of ['analysis_queued', 'analyzing']) {
            const job = {
                ...makeJob({ stage, sessions_imported: 5 }),
                import_result: mockResult,
            } as unknown as PipelineJobStatus
            const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })
            expect(wrapper.find('.job-result-summary').exists()).toBe(false)
            wrapper.unmount()
        }

        const doneJob = {
            ...makeJob({ stage: 'done' }),
            import_result: mockResult,
        } as unknown as PipelineJobStatus
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [doneJob] } })
        expect(wrapper.find('.job-result-summary').exists()).toBe(true)
    })

    it('test_analysis_queued_shows_sessions_imported_count', () => {
        const job = makeJob({ stage: 'analysis_queued', sessions_imported: 3 })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('3 session(s) imported')
    })

    it('test_unknown_stage_renders_question_mark_icon', () => {
        const job = makeJob({ stage: 'unknown' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.find('.job-icon').text()).toBe('?')
    })

    // ---- Apple Health job_type ----

    it('test_health_upload_job_shows_apple_health_label', () => {
        const job = makeJob({ job_type: 'health_upload', stage: 'queued' })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.find('.job-label').text()).toBe('Apple Health')
    })

    it('test_done_health_job_renders_samples_nights_duplicates_summary', () => {
        const job = {
            ...makeJob({ job_type: 'health_upload', stage: 'done' }),
            health_import_result: { inserted: 10, skipped: 0, nights_recomputed: 2 },
        } as unknown as ReturnType<typeof makeJob>
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.find('.job-result-summary').exists()).toBe(true)
        expect(wrapper.text()).toContain('10 samples')
        expect(wrapper.text()).toContain('2 nights')
        expect(wrapper.text()).toContain('0 duplicates')
    })

    it('test_cpap_done_job_does_not_show_health_result_summary', () => {
        // Regression: a CPAP done job with no import_result must not render
        // the health-specific .job-result-summary block.
        const job = makeJob({ job_type: 'upload', stage: 'done', sessions_imported: 4 })
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.text()).toContain('4 session(s) imported')
        expect(wrapper.find('.job-result-summary').exists()).toBe(false)
    })

    it('test_health_upload_non_done_stage_does_not_show_health_result_summary', () => {
        // The health_import_result block is gated on stage === 'done'.
        // An importing-stage health_upload job with a partial health_import_result
        // (e.g. from a cancel mid-flight) must not render .job-result-summary.
        const job = {
            ...makeJob({ job_type: 'health_upload', stage: 'importing' }),
            health_import_result: { inserted: 5, skipped: 2, nights_recomputed: 1 },
        } as unknown as PipelineJobStatus
        const wrapper = mount(ImportJobsPanel, { props: { jobs: [job] } })

        expect(wrapper.find('.job-result-summary').exists()).toBe(false)
    })
})
