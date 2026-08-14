import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

vi.mock('@/composables/useAuth')
vi.mock('@/composables/useDateFormat')

import { makeDateFormatMock as baseDateFormatMock } from './helpers/mockUseAuth'
vi.mock('@/api/admin')

// Stub DeleteConfirmDialog — avoid portal/jsdom incompatibilities.
// data-title lets tests distinguish the per-row and reset-all dialogs.
vi.mock('@/components/DeleteConfirmDialog.vue', () => ({
    default: defineComponent({
        name: 'DeleteConfirmDialog',
        props: ['visible', 'title', 'message', 'loading', 'deleting', 'confirmLabel'],
        emits: ['update:visible', 'confirm'],
        template: `<button v-if="visible" class="stub-confirm" :data-title="title" @click="$emit('confirm')">Confirm</button>`,
    }),
}))

import AdminMcpView from '@/views/AdminMcpView.vue'
import { useDateFormat } from '@/composables/useDateFormat'
import {
    getMcpStatus,
    listGoogleBindings,
    resetGoogleBinding,
    resetAllGoogleBindings,
} from '@/api/admin'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MCP_STATUS = {
    enabled: true,
    endpoint_url: 'https://mcp.example.com',
    transport: 'streamable-http',
    auth_provider: 'google',
    linked_google_identities: 2,
    disabled_reason: null,
}

const BINDING_A = {
    user_id: 1,
    user_email: 'alice@example.com',
    display_name: 'Alice',
    google_email: 'alice@gmail.com',
    linked_at: '2026-01-15T12:00:00Z',
    has_password: true,
}

const BINDING_B = {
    user_id: 2,
    user_email: 'bob@example.com',
    display_name: null,
    google_email: null,
    linked_at: '2026-02-20T08:30:00Z',
    has_password: false,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeDateFormatMock() {
    vi.mocked(useDateFormat).mockReturnValue(baseDateFormatMock() as never)
}

async function mountAndLoad() {
    const wrapper = mount(AdminMcpView)
    await flushPromises()
    return wrapper
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AdminMcpView — bindings table', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeDateFormatMock()
        vi.mocked(getMcpStatus).mockResolvedValue(MCP_STATUS as never)
        vi.mocked(listGoogleBindings).mockResolvedValue([BINDING_A, BINDING_B])
    })

    it('test_bindings_table_renders_user_email_and_google_email', async () => {
        const wrapper = await mountAndLoad()

        expect(wrapper.text()).toContain('alice@example.com')
        expect(wrapper.text()).toContain('alice@gmail.com')
        expect(wrapper.text()).toContain('bob@example.com')
        // BINDING_B has null google_email — should show em dash
        expect(wrapper.text()).toContain('—')
    })

    it('test_reset_button_disabled_when_has_password_false', async () => {
        const wrapper = await mountAndLoad()

        const resetBtns = wrapper.findAll('button').filter((b) => b.text().trim() === 'Reset')
        // Two bindings → two reset buttons
        expect(resetBtns.length).toBe(2)

        // BINDING_A has_password true → enabled
        expect((resetBtns[0].element as HTMLButtonElement).disabled).toBe(false)
        // BINDING_B has_password false → disabled
        expect((resetBtns[1].element as HTMLButtonElement).disabled).toBe(true)
    })
})

describe('AdminMcpView — per-row reset', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeDateFormatMock()
        vi.mocked(getMcpStatus).mockResolvedValue(MCP_STATUS as never)
        vi.mocked(listGoogleBindings).mockResolvedValue([BINDING_A])
    })

    it('test_confirm_per_row_reset_calls_resetGoogleBinding_and_refetches', async () => {
        vi.mocked(resetGoogleBinding).mockResolvedValueOnce({ message: 'ok' })
        // Return updated list (empty) after reset
        vi.mocked(listGoogleBindings).mockResolvedValueOnce([BINDING_A]).mockResolvedValueOnce([])

        const wrapper = await mountAndLoad()

        // Click the Reset button to open the dialog
        const resetBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset')
        expect(resetBtn).toBeTruthy()
        await resetBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Dialog should now be visible
        expect(wrapper.find('.stub-confirm').exists()).toBe(true)

        // Confirm the reset
        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        expect(resetGoogleBinding).toHaveBeenCalledWith(BINDING_A.user_id)
        // listGoogleBindings is called once on mount and once after reset
        expect(listGoogleBindings).toHaveBeenCalledTimes(2)
    })
})

describe('AdminMcpView — reset all', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeDateFormatMock()
        vi.mocked(getMcpStatus).mockResolvedValue(MCP_STATUS as never)
        vi.mocked(listGoogleBindings).mockResolvedValue([BINDING_A, BINDING_B])
    })

    it('test_confirm_reset_all_calls_api_and_shows_feedback', async () => {
        vi.mocked(resetAllGoogleBindings).mockResolvedValueOnce({ reset: 1, skipped: 1 })
        vi.mocked(listGoogleBindings)
            .mockResolvedValueOnce([BINDING_A, BINDING_B])
            .mockResolvedValueOnce([])

        const wrapper = await mountAndLoad()

        // Click "Reset all" button
        const resetAllBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset all')
        expect(resetAllBtn).toBeTruthy()
        await resetAllBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Dialog should appear
        const confirmBtn = wrapper
            .findAll('.stub-confirm')
            .find((b) => b.attributes('data-title') === 'Reset all Google bindings')
        expect(confirmBtn).toBeTruthy()

        await confirmBtn!.trigger('click')
        await flushPromises()

        expect(resetAllGoogleBindings).toHaveBeenCalledTimes(1)
        expect(wrapper.find('.binding-feedback').text()).toContain('Reset Google access for 1 user')
        expect(wrapper.find('.binding-feedback').text()).toContain('skipped 1 without a password')
    })
})

describe('AdminMcpView — empty state', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeDateFormatMock()
        vi.mocked(getMcpStatus).mockResolvedValue(MCP_STATUS as never)
        vi.mocked(listGoogleBindings).mockResolvedValue([])
    })

    it('test_empty_bindings_shows_empty_state_message', async () => {
        const wrapper = await mountAndLoad()
        expect(wrapper.text()).toContain('No Google accounts linked.')
    })

    it('test_reset_all_button_hidden_when_no_bindings', async () => {
        const wrapper = await mountAndLoad()
        const resetAllBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset all')
        expect(resetAllBtn).toBeUndefined()
    })
})

describe('AdminMcpView — error paths', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeDateFormatMock()
        vi.mocked(getMcpStatus).mockResolvedValue(MCP_STATUS as never)
    })

    it('test_bindings_fetch_error_shows_alert', async () => {
        vi.mocked(listGoogleBindings).mockRejectedValue(new Error('network error'))

        const wrapper = await mountAndLoad()

        // bindingsError gets the raw Error message; just verify the alert element renders
        const alert = wrapper.find('.error-state[role="alert"]')
        expect(alert.exists()).toBe(true)
    })

    it('test_per_row_reset_error_shows_inline_error', async () => {
        vi.mocked(listGoogleBindings).mockResolvedValue([BINDING_A])
        vi.mocked(resetGoogleBinding).mockRejectedValue(new Error('server error'))

        const wrapper = await mountAndLoad()

        // Click Reset to open dialog, then confirm
        const resetBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset')
        expect(resetBtn).toBeTruthy()
        await resetBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        await wrapper.find('.stub-confirm').trigger('click')
        await flushPromises()

        const errorEl = wrapper.find('.binding-error')
        expect(errorEl.exists()).toBe(true)
        expect(errorEl.attributes('role')).toBe('alert')
        expect(errorEl.text()).toContain('server error')
    })
})
