import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { defineComponent } from 'vue'

vi.mock('@/composables/useAuth')
vi.mock('@/api/db')
vi.mock('@/utils/formatting', () => ({ formatDateShort: (d: string) => d }))

// Stub DeleteConfirmDialog: renders a plain button visible when `visible` is
// true; fires `confirm` on click.  Avoids portal/jsdom incompatibilities while
// still testing the view's wiring (dialog open state, confirm handler, etc.).
vi.mock('@/components/DeleteConfirmDialog.vue', () => ({
    default: defineComponent({
        name: 'DeleteConfirmDialog',
        props: ['visible', 'title', 'message', 'loading', 'deleting', 'confirmPhrase'],
        emits: ['update:visible', 'confirm'],
        template: `<button v-if="visible" class="stub-confirm" @click="$emit('confirm')">Confirm</button>`,
    }),
}))

import { makeAuthMock as baseMakeAuthMock } from './helpers/mockUseAuth'
import DatabaseView from '@/views/DatabaseView.vue'
import { useAuth } from '@/composables/useAuth'
import { getDbStats, resetDb } from '@/api/db'

const DB_STATS = {
    size_mb: 42.5,
    profile_count: 1,
    device_count: 1,
    session_count: 30,
    day_count: 30,
    event_count: 200,
    waveform_count: 100,
    analysis_count: 25,
    pattern_count: 10,
    sessions_with_waveforms: 28,
    sessions_with_events: 30,
    sessions_with_analysis: 20,
    analyzable_session_count: 28,
    waveform_coverage_pct: 93.3,
    event_coverage_pct: 100.0,
    analysis_coverage_pct: 71.4,
    first_session: '2025-01-01T22:00:00',
    last_session: '2025-08-07T22:00:00',
}

const RESET_RESULT_DATA_ONLY = {
    status: 'success',
    tables_cleared: { sessions: 30, devices: 1 },
    total_rows_deleted: 31,
    size_before_mb: 42.5,
    size_after_mb: null,
    vacuum_scheduled: true,
    bootstrap_invite_url: null,
}

const RESET_RESULT_FULL = {
    status: 'success',
    tables_cleared: { users: 1, sessions: 30, devices: 1 },
    total_rows_deleted: 32,
    size_before_mb: 42.5,
    size_after_mb: null,
    vacuum_scheduled: true,
    bootstrap_invite_url: '/invite#abc123',
}

function makeAuthMock(overrides: Record<string, unknown> = {}) {
    vi.mocked(useAuth).mockReturnValue(
        baseMakeAuthMock({
            isLocal: ref(false),
            role: ref('admin'),
            isAuthenticated: ref(true),
            ...overrides,
        }) as never,
    )
}

describe('DatabaseView — danger zone', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        vi.mocked(getDbStats).mockResolvedValue(DB_STATS as never)
        vi.mocked(resetDb).mockResolvedValue(RESET_RESULT_DATA_ONLY as never)
    })

    async function mountAndLoad() {
        const wrapper = mount(DatabaseView)
        await flushPromises()
        return wrapper
    }

    it('test_danger_zone_visible_for_admin', async () => {
        const wrapper = await mountAndLoad()
        expect(wrapper.find('.danger-card').exists()).toBe(true)
    })

    it('test_danger_zone_visible_in_local_mode', async () => {
        makeAuthMock({ isLocal: ref(true), role: ref('admin') })
        const wrapper = await mountAndLoad()
        expect(wrapper.find('.danger-card').exists()).toBe(true)
    })

    it('test_danger_zone_hidden_for_member', async () => {
        makeAuthMock({ role: ref('member') })
        const wrapper = await mountAndLoad()
        expect(wrapper.find('.danger-card').exists()).toBe(false)
    })

    it('test_include_accounts_checkbox_shown_in_multiuser_mode', async () => {
        const wrapper = await mountAndLoad()
        expect(wrapper.find('.include-accounts-checkbox').exists()).toBe(true)
    })

    it('test_include_accounts_checkbox_hidden_in_local_mode', async () => {
        makeAuthMock({ isLocal: ref(true), role: ref('admin') })
        const wrapper = await mountAndLoad()
        expect(wrapper.find('.include-accounts-checkbox').exists()).toBe(false)
    })

    it('test_reset_button_opens_dialog_and_confirm_calls_resetDb', async () => {
        const wrapper = await mountAndLoad()

        // The stub-confirm button only renders when the dialog is open.
        expect(wrapper.find('.stub-confirm').exists()).toBe(false)

        // Click Reset — sets resetDialogOpen = true.
        await wrapper.find('.danger-card button').trigger('click')
        await wrapper.vm.$nextTick()

        // Dialog now open.
        expect(wrapper.find('.stub-confirm').exists()).toBe(true)

        // Fire confirm — triggers handleReset.
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        expect(resetDb).toHaveBeenCalledWith({ include_accounts: false })
    })

    it('test_data_only_reset_does_not_show_invite_banner', async () => {
        const wrapper = await mountAndLoad()

        await wrapper.find('.danger-card button').trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        expect(wrapper.find('.invite-banner').exists()).toBe(false)
    })

    it('test_full_reset_shows_invite_banner_and_does_not_reload', async () => {
        vi.mocked(resetDb).mockResolvedValueOnce(RESET_RESULT_FULL as never)

        const wrapper = await mountAndLoad()

        // Check the include_accounts checkbox.
        await wrapper.find('.include-accounts-checkbox').setValue(true)
        await wrapper.vm.$nextTick()

        await wrapper.find('.danger-card button').trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        // Invite banner must be visible with the URL.
        expect(wrapper.find('.invite-banner').exists()).toBe(true)
        expect(wrapper.find('.invite-url-text').text()).toContain('/invite#abc123')

        // getDbStats must only have been called once (initial load) — no
        // reload after full reset because the dead session would redirect away.
        expect(getDbStats).toHaveBeenCalledTimes(1)
    })

    it('test_full_reset_does_not_set_error_when_invite_url_present', async () => {
        vi.mocked(resetDb).mockResolvedValueOnce(RESET_RESULT_FULL as never)

        const wrapper = await mountAndLoad()

        await wrapper.find('.include-accounts-checkbox').setValue(true)
        await wrapper.vm.$nextTick()
        await wrapper.find('.danger-card button').trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        // Invite banner visible; no error shown.
        expect(wrapper.find('.invite-banner').exists()).toBe(true)
        expect(wrapper.find('.error-state').exists()).toBe(false)
    })

    it('test_reset_error_shows_when_no_invite_url', async () => {
        vi.mocked(resetDb).mockRejectedValueOnce(new Error('Server error'))

        const wrapper = await mountAndLoad()

        await wrapper.find('.danger-card button').trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        expect(wrapper.find('.error-state').exists()).toBe(true)
        expect(wrapper.find('.error-state').text()).toContain('Server error')
    })
})
