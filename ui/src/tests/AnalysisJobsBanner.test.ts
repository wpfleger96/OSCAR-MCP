import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

import AnalysisJobsBanner from '@/components/AnalysisJobsBanner.vue'
import type { AnalysisJobInfo } from '@/api/analysis'

function makeJob(overrides: Partial<AnalysisJobInfo> = {}): AnalysisJobInfo {
    const nowSecs = Date.now() / 1000
    return {
        job_id: 'job-1',
        state: 'succeeded',
        source: 'import',
        session_count: 5,
        progress_completed: 5,
        progress_total: 5,
        error_message: null,
        created_at: nowSecs,
        started_at: nowSecs,
        finished_at: nowSecs,
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

    it('test_monotonic_style_timestamp_does_not_render_as_just_now', () => {
        // Guard the PR #290 bug: the API must send wall-clock epoch seconds. A
        // time.monotonic() value (seconds since boot, e.g. ~12345) maps to a
        // 1970-era instant, so it must NOT render as a recent relative time.
        const wrapper = mount(AnalysisJobsBanner, {
            props: { jobs: [makeJob({ created_at: 12345.6, finished_at: 12345.6 })] },
        })
        const text = wrapper.find('.job-timestamp').text()
        expect(text).not.toBe('just now')
        expect(text).not.toMatch(/\bago$/)
    })
})
