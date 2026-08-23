import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

import AnalysisJobsBanner from '@/components/AnalysisJobsBanner.vue'
import type { AnalysisJobStatus } from '@/types'

function makeJob(overrides: Partial<AnalysisJobStatus> = {}): AnalysisJobStatus {
    const now = new Date().toISOString()
    return {
        job_id: 'job-1',
        state: 'succeeded',
        source: 'import',
        session_count: 5,
        progress_completed: 5,
        progress_total: 5,
        error_message: null,
        created_at: now,
        started_at: now,
        finished_at: now,
        owner_user_id: 1,
        ...overrides,
    }
}

describe('AnalysisJobsBanner', () => {
    it('test_terminal_job_renders_finished_timestamp', () => {
        const wrapper = mount(AnalysisJobsBanner, { props: { jobs: [makeJob()] } })
        const ts = wrapper.find('.job-timestamp')
        expect(ts.exists()).toBe(true)
        expect(ts.text()).toBe('just now')
    })

    it('test_running_job_falls_back_to_created_timestamp', () => {
        const wrapper = mount(AnalysisJobsBanner, {
            props: { jobs: [makeJob({ state: 'running', finished_at: null })] },
        })
        const ts = wrapper.find('.job-timestamp')
        expect(ts.exists()).toBe(true)
        expect(ts.text()).toBe('just now')
    })
})
