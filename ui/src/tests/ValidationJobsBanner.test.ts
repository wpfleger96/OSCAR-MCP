import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

import ValidationJobsBanner from '@/components/validation/ValidationJobsBanner.vue'
import type { ValidationRunStatus, ValidatorType } from '@/types'

function makeRun(overrides: Partial<ValidationRunStatus> = {}): ValidationRunStatus {
    const now = new Date().toISOString()
    return {
        run_id: 1,
        validator_type: 'rera' as ValidatorType,
        state: 'running',
        reused: false,
        date_from: '2026-01-01',
        date_to: '2026-01-07',
        created_at: now,
        started_at: now,
        finished_at: null,
        job_id: 'job-1',
        error_message: null,
        owner_user_id: 1,
        engine_identity: {},
        validator_params: {},
        ...overrides,
    } as ValidationRunStatus
}

describe('ValidationJobsBanner', () => {
    it('test_active_run_shows_cancel_affordance', () => {
        const wrapper = mount(ValidationJobsBanner, {
            props: { runs: [makeRun({ state: 'running' })] },
        })
        expect(wrapper.find('button[title="Cancel run"]').exists()).toBe(true)
        expect(wrapper.text()).toContain('Running…')
    })

    it('test_queued_run_shows_cancel_affordance', () => {
        const wrapper = mount(ValidationJobsBanner, {
            props: { runs: [makeRun({ state: 'queued' })] },
        })
        expect(wrapper.find('button[title="Cancel run"]').exists()).toBe(true)
        expect(wrapper.text()).toContain('Queued')
    })

    it('test_terminal_run_has_no_cancel_affordance', () => {
        const wrapper = mount(ValidationJobsBanner, {
            props: {
                runs: [makeRun({ state: 'succeeded', finished_at: new Date().toISOString() })],
            },
        })
        expect(wrapper.find('button[title="Cancel run"]').exists()).toBe(false)
    })

    it('test_cancel_click_emits_run_id', async () => {
        const wrapper = mount(ValidationJobsBanner, {
            props: { runs: [makeRun({ run_id: 42, state: 'running' })] },
        })
        await wrapper.find('button[title="Cancel run"]').trigger('click')
        expect(wrapper.emitted('cancel')?.[0]).toEqual([42])
    })

    it('test_label_names_validator_and_date_range', () => {
        const wrapper = mount(ValidationJobsBanner, {
            props: { runs: [makeRun({ validator_type: 'fl' as ValidatorType })] },
        })
        expect(wrapper.text()).toContain('FL vs FLG: 2026-01-01 → 2026-01-07')
    })
})
