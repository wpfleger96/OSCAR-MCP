import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'

vi.mock('@/composables/useAuth')
vi.mock('@/composables/useDateFormat')

import {
    makeAuthMock as baseMakeAuthMock,
    makeDateFormatMock as baseDateFormatMock,
} from './helpers/mockUseAuth'
vi.mock('@/api/admin')

// Stub table UI — pass slot content through so functional elements are accessible.
vi.mock('@/components/ui/table', () => ({
    Table: { template: '<div data-table><slot /></div>' },
    TableHeader: { template: '<div><slot /></div>' },
    TableBody: { template: '<div><slot /></div>' },
    TableRow: { template: '<div data-row><slot /></div>' },
    TableHead: { template: '<div><slot /></div>' },
    TableCell: { template: '<div><slot /></div>' },
}))
vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button><slot /></button>' },
}))

import AdminUsersView from '@/views/AdminUsersView.vue'
import { useAuth } from '@/composables/useAuth'
import { useDateFormat } from '@/composables/useDateFormat'
import { listUsers, updateUser, listInvites, createInvite, disableUser } from '@/api/admin'
import { adminResetTotp } from '@/api/totp'

vi.mock('@/api/totp')

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const USER_A = {
    id: 1,
    email: 'alice@example.com',
    display_name: 'Alice',
    role: 'member',
    disabled: false,
    created_at: '2026-01-01T00:00:00Z',
    has_password: true,
    auth_providers: [],
    last_login_at: null,
    totp_enabled: false,
}

const USER_B = {
    id: 2,
    email: 'bob@example.com',
    display_name: 'Bob',
    role: 'admin',
    disabled: false,
    created_at: '2026-01-01T00:00:00Z',
    has_password: false,
    auth_providers: ['google'],
    last_login_at: '2026-07-01T12:00:00Z',
    totp_enabled: false,
}

const SELF_USER = {
    id: 99,
    email: 'me@example.com',
    display_name: 'Me',
    role: 'admin',
    disabled: false,
    created_at: '2026-01-01T00:00:00Z',
    has_password: true,
    auth_providers: [],
    last_login_at: null,
    totp_enabled: false,
}

const OTHER_USER = {
    id: 2,
    email: 'other@example.com',
    display_name: 'Other',
    role: 'member',
    disabled: false,
    created_at: '2026-01-01T00:00:00Z',
    has_password: false,
    auth_providers: ['google'],
    last_login_at: '2026-06-15T08:00:00Z',
    totp_enabled: false,
}

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

function makeAuthMock(userId = 1) {
    vi.mocked(useAuth).mockReturnValue(
        baseMakeAuthMock({
            user: ref({
                id: userId,
                email: 'test@example.com',
                display_name: null,
                role: 'admin',
            }) as never,
            role: ref('admin') as never,
            isAuthenticated: ref(true) as never,
        }) as never,
    )
}

function makeDateFormatMock() {
    vi.mocked(useDateFormat).mockReturnValue(baseDateFormatMock() as never)
}

// ---------------------------------------------------------------------------
// Helper: mount the view and wait for both useApiLoad calls to complete.
// ---------------------------------------------------------------------------

async function mountAndLoad() {
    const wrapper = mount(AdminUsersView)
    await flushPromises()
    return wrapper
}

// Trigger the role-change handler on the Nth role select (0-indexed).
// We must set the underlying element value before dispatching 'change' because
// the handler reads ($event.target as HTMLSelectElement).value.
async function triggerRoleChange(
    wrapper: ReturnType<typeof mount>,
    selectIndex: number,
    newRole: string,
) {
    const selects = wrapper.findAll('.role-select')
    const el = selects[selectIndex].element as HTMLSelectElement
    el.value = newRole
    await selects[selectIndex].trigger('change')
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AdminUsersView — role changes', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(listUsers).mockResolvedValue([USER_A])
        vi.mocked(listInvites).mockResolvedValue([])
    })

    it('test_role_change_failure_reverts_select_and_shows_row_error', async () => {
        vi.mocked(updateUser).mockRejectedValueOnce(new Error('Forbidden'))

        const wrapper = await mountAndLoad()

        await triggerRoleChange(wrapper, 0, 'admin')
        await flushPromises()

        // Select must revert to the original role.
        const select = wrapper.find('.role-select').element as HTMLSelectElement
        expect(select.value).toBe('member')

        // Row-scoped error must appear.
        expect(wrapper.find('.row-error').exists()).toBe(true)
        expect(wrapper.find('.row-error').text()).toContain('Forbidden')
    })

    it('test_role_change_success_on_row_A_does_not_clear_preexisting_error_on_row_B', async () => {
        vi.mocked(listUsers).mockResolvedValue([USER_A, USER_B])
        const wrapper = await mountAndLoad()

        // Produce a row error on B (index 1) via a failed role change.
        vi.mocked(updateUser).mockRejectedValueOnce(new Error('Network'))
        await triggerRoleChange(wrapper, 1, 'demo')
        await flushPromises()

        // B has an error; A does not.
        expect(wrapper.findAll('.row-error').length).toBe(1)

        // Now succeed on A (index 0). reloadUsers fires — return both users again.
        vi.mocked(updateUser).mockResolvedValueOnce({ message: 'ok' })
        vi.mocked(listUsers).mockResolvedValueOnce([USER_A, USER_B])
        await triggerRoleChange(wrapper, 0, 'admin')
        await flushPromises()

        // B's error must survive A's successful change.
        expect(wrapper.findAll('.row-error').length).toBe(1)
        expect(wrapper.find('.row-error').text()).toContain('Network')
    })
})

describe('AdminUsersView — invite TTL validation', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(listUsers).mockResolvedValue([])
        vi.mocked(listInvites).mockResolvedValue([])
    })

    async function submitInviteForm(wrapper: ReturnType<typeof mount>, ttlValue: string) {
        await wrapper.find('input[type="email"]').setValue('test@example.com')
        const ttlInput = wrapper.find('#ttl-days')
        await ttlInput.setValue(ttlValue)
        await wrapper.find('.invite-form').trigger('submit')
        await wrapper.vm.$nextTick()
    }

    it('test_ttl_NaN_via_cleared_field_shows_error_and_does_not_call_createInvite', async () => {
        const wrapper = await mountAndLoad()
        await submitInviteForm(wrapper, '')
        expect(wrapper.find('[role="alert"]').text()).toContain(
            'Expiry must be between 1 and 30 days',
        )
        expect(createInvite).not.toHaveBeenCalled()
    })

    it('test_ttl_zero_shows_error_and_does_not_call_createInvite', async () => {
        const wrapper = await mountAndLoad()
        await submitInviteForm(wrapper, '0')
        expect(wrapper.find('[role="alert"]').text()).toContain(
            'Expiry must be between 1 and 30 days',
        )
        expect(createInvite).not.toHaveBeenCalled()
    })

    it('test_ttl_31_shows_error_and_does_not_call_createInvite', async () => {
        const wrapper = await mountAndLoad()
        await submitInviteForm(wrapper, '31')
        expect(wrapper.find('[role="alert"]').text()).toContain(
            'Expiry must be between 1 and 30 days',
        )
        expect(createInvite).not.toHaveBeenCalled()
    })
})

describe('AdminUsersView — invite create success flow', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(listUsers).mockResolvedValue([])
        vi.mocked(listInvites).mockResolvedValue([])
    })

    it('test_invite_create_success_shows_url_in_readonly_input_and_create_another_resets_form', async () => {
        const inviteResponse = {
            id: 1,
            email: 'new@example.com',
            role: 'member',
            expires_at: '2026-09-01T00:00:00Z',
            invite_url: 'https://example.com/invite/MYTOKEN',
        }
        vi.mocked(createInvite).mockResolvedValueOnce(inviteResponse)
        vi.mocked(listInvites).mockResolvedValue([])

        const wrapper = await mountAndLoad()

        // Fill and submit the invite form.
        await wrapper.find('input[type="email"]').setValue('new@example.com')
        await wrapper.find('.invite-form').trigger('submit')
        await flushPromises()

        // Invite URL readonly input should appear with the URL.
        const readonlyInput = wrapper.find('input[readonly]')
        expect(readonlyInput.exists()).toBe(true)
        expect((readonlyInput.element as HTMLInputElement).value).toBe(
            'https://example.com/invite/MYTOKEN',
        )

        // Caption should be shown.
        expect(wrapper.find('.invite-caption').exists()).toBe(true)

        // Click "Create another" — form should reset, readonly input gone.
        const createAnotherBtn = wrapper
            .findAll('button')
            .find((b) => b.text().trim() === 'Create another')
        expect(createAnotherBtn).toBeDefined()
        await createAnotherBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        expect(wrapper.find('input[readonly]').exists()).toBe(false)
        expect(wrapper.find('.invite-form').exists()).toBe(true)
    })
})

describe('AdminUsersView — own-row disable button', () => {
    it('test_disable_button_absent_on_current_users_own_row_present_on_others', async () => {
        vi.resetAllMocks()
        // Current user is SELF_USER (id=99).
        makeAuthMock(99)
        makeDateFormatMock()
        vi.mocked(listUsers).mockResolvedValue([SELF_USER, OTHER_USER])
        vi.mocked(listInvites).mockResolvedValue([])

        const wrapper = await mountAndLoad()

        // There should be exactly one Disable button (for OTHER_USER, not SELF_USER).
        const disableBtns = wrapper.findAll('button').filter((b) => b.text().trim() === 'Disable')
        expect(disableBtns.length).toBe(1)

        // The disable button must NOT call disableUser(99).
        await disableBtns[0].trigger('click')
        await flushPromises()
        expect(disableUser).not.toHaveBeenCalledWith(99)
    })
})

describe('AdminUsersView — busyUserIds in-flight locking', () => {
    it('test_controls_disabled_for_user_while_request_is_in_flight', async () => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(listUsers).mockResolvedValue([USER_A])
        vi.mocked(listInvites).mockResolvedValue([])

        // updateUser never resolves — simulates a stuck in-flight request.
        vi.mocked(updateUser).mockReturnValue(new Promise(() => {}))

        const wrapper = await mountAndLoad()

        // Trigger a role change — handler adds userId to busyUserIds before awaiting.
        await triggerRoleChange(wrapper, 0, 'admin')
        // trigger() already awaits $nextTick; the promise is still pending.

        const select = wrapper.find('.role-select')
        expect(select.attributes('disabled')).toBeDefined()
    })
})

// ---------------------------------------------------------------------------
// 2FA badge and reset action
// ---------------------------------------------------------------------------

describe('AdminUsersView — 2FA badge and reset', () => {
    const USER_WITH_2FA = {
        ...OTHER_USER,
        id: 3,
        email: 'totp@example.com',
        totp_enabled: true,
    }

    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(listInvites).mockResolvedValue([])
    })

    it('test_2fa_badge_shown_for_user_with_totp_enabled', async () => {
        vi.mocked(listUsers).mockResolvedValue([USER_WITH_2FA])
        const wrapper = await mountAndLoad()
        expect(wrapper.text()).toContain('2FA')
    })

    it('test_2fa_badge_not_shown_for_user_without_totp', async () => {
        vi.mocked(listUsers).mockResolvedValue([USER_A])
        const wrapper = await mountAndLoad()
        const badges = wrapper.findAll('.status-badge--totp')
        expect(badges.length).toBe(0)
    })

    it('test_reset_2fa_action_calls_adminResetTotp_and_reloads', async () => {
        vi.mocked(listUsers).mockResolvedValue([USER_WITH_2FA])
        vi.mocked(adminResetTotp).mockResolvedValueOnce({ message: 'TOTP reset' })
        // After reset, return user without TOTP
        vi.mocked(listUsers)
            .mockResolvedValueOnce([USER_WITH_2FA])
            .mockResolvedValueOnce([{ ...USER_WITH_2FA, totp_enabled: false }])

        const wrapper = await mountAndLoad()

        // Click "Reset 2FA" to enter confirm state
        const resetBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset 2FA')
        expect(resetBtn).toBeTruthy()
        await resetBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Confirm row shows "Reset 2FA?" with Yes/No
        expect(wrapper.text()).toContain('Reset 2FA?')

        // Click Yes to execute reset
        const yesBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Yes')
        expect(yesBtn).toBeTruthy()
        await yesBtn!.trigger('click')
        await flushPromises()

        expect(adminResetTotp).toHaveBeenCalledWith(USER_WITH_2FA.id)
        // List should reload after reset
        expect(listUsers).toHaveBeenCalledTimes(2)
    })

    it('test_reset_2fa_error_shows_row_error', async () => {
        vi.mocked(listUsers).mockResolvedValue([USER_WITH_2FA])
        vi.mocked(adminResetTotp).mockRejectedValueOnce(new Error('Reset failed'))

        const wrapper = await mountAndLoad()

        const resetBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Reset 2FA')
        await resetBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        const yesBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Yes')
        await yesBtn!.trigger('click')
        await flushPromises()

        expect(wrapper.find('.row-error').text()).toContain('Reset failed')
    })
})
