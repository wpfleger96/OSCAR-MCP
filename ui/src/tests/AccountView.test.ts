import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'

vi.mock('@/composables/useAuth')
vi.mock('@/composables/useDateFormat')
vi.mock('vue-router')

import {
    makeAuthMock as baseMakeAuthMock,
    makeDateFormatMock as baseDateFormatMock,
} from './helpers/mockUseAuth'
vi.mock('@/api/me')

import AccountView from '@/views/AccountView.vue'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import { useAuth } from '@/composables/useAuth'
import { useDateFormat } from '@/composables/useDateFormat'
import { useRouter } from 'vue-router'
import { getMe, changePassword, getPreferences, unlinkGoogle } from '@/api/me'

const ME_WITH_PASSWORD = {
    id: 1,
    email: 'user@example.com',
    display_name: 'Test User',
    has_password: true,
    google_linked: false,
    role: 'member',
}

const ME_WITHOUT_PASSWORD = {
    id: 1,
    email: 'google@example.com',
    display_name: null,
    has_password: false,
    google_linked: true,
    role: 'member',
}

const PREFS = { landing_page: 'dashboard' as const, date_format: 'iso' as const }

function makeAuthMock(role = 'member') {
    vi.mocked(useAuth).mockReturnValue(
        baseMakeAuthMock({
            role: ref(role) as never,
            isAuthenticated: ref(true) as never,
        }) as never,
    )
}

function makeDateFormatMock() {
    vi.mocked(useDateFormat).mockReturnValue(baseDateFormatMock() as never)
}

describe('AccountView — password form', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        makeAuthMock()
        makeDateFormatMock()
        vi.mocked(getMe).mockResolvedValue(ME_WITH_PASSWORD)
        vi.mocked(getPreferences).mockResolvedValue(PREFS as never)
    })

    async function mountAndLoad() {
        const wrapper = mount(AccountView)
        await flushPromises()
        return wrapper
    }

    it('test_password_mismatch_shows_error_and_does_not_call_changePassword', async () => {
        const wrapper = await mountAndLoad()

        await wrapper.find('#new-password').setValue('abc')
        await wrapper.find('#confirm-password').setValue('xyz')
        // The password stacked-form is the first .stacked-form in the template
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await wrapper.vm.$nextTick()

        expect(wrapper.find('[role="alert"]').text()).toContain('Passwords do not match')
        expect(changePassword).not.toHaveBeenCalled()
    })

    it('test_empty_new_password_shows_error_and_does_not_call_changePassword', async () => {
        const wrapper = await mountAndLoad()

        // Leave #new-password at its default empty value; just submit
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await wrapper.vm.$nextTick()

        expect(wrapper.find('[role="alert"]').text()).toContain('New password cannot be empty')
        expect(changePassword).not.toHaveBeenCalled()
    })

    it('test_successful_change_shows_success_clears_fields_and_calls_changePassword_with_current_password', async () => {
        vi.mocked(changePassword).mockResolvedValueOnce({ message: 'ok' })

        const wrapper = await mountAndLoad()
        await wrapper.find('#current-password').setValue('oldpass')
        await wrapper.find('#new-password').setValue('newpass123')
        await wrapper.find('#confirm-password').setValue('newpass123')
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await flushPromises()

        expect(changePassword).toHaveBeenCalledWith({
            current_password: 'oldpass',
            new_password: 'newpass123',
        })
        expect(wrapper.find('.form-success').text()).toBe('Password updated')
        expect((wrapper.find('#new-password').element as HTMLInputElement).value).toBe('')
        expect((wrapper.find('#confirm-password').element as HTMLInputElement).value).toBe('')
    })

    it('test_google_only_account_hides_current_password_field_and_omits_it_from_changePassword', async () => {
        vi.mocked(getMe).mockResolvedValue(ME_WITHOUT_PASSWORD)
        vi.mocked(changePassword).mockResolvedValueOnce({ message: 'ok' })

        const wrapper = await mountAndLoad()

        expect(wrapper.find('#current-password').exists()).toBe(false)

        await wrapper.find('#new-password').setValue('newpass123')
        await wrapper.find('#confirm-password').setValue('newpass123')
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await flushPromises()

        expect(changePassword).toHaveBeenCalledWith({ new_password: 'newpass123' })
        expect(changePassword).not.toHaveBeenCalledWith(
            expect.objectContaining({ current_password: expect.anything() }),
        )
    })

    it('test_changePassword_rejection_shows_error_and_suppresses_success', async () => {
        vi.mocked(changePassword).mockRejectedValueOnce(new Error('Unauthorized'))

        const wrapper = await mountAndLoad()
        await wrapper.find('#current-password').setValue('wrong')
        await wrapper.find('#new-password').setValue('newpass123')
        await wrapper.find('#confirm-password').setValue('newpass123')
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await flushPromises()

        expect(wrapper.find('[role="alert"]').text()).toContain('Unauthorized')
        expect(wrapper.find('.form-success').exists()).toBe(false)
    })

    it('test_success_flips_has_password_locally_without_refetch', async () => {
        // A successful change must not depend on a follow-up network fetch:
        // has_password is updated locally, so a flaky connection can't hide
        // the success banner or blank the page into the meError state.
        vi.mocked(getMe).mockResolvedValue(ME_WITHOUT_PASSWORD)
        vi.mocked(changePassword).mockResolvedValueOnce({ message: 'ok' })

        const wrapper = await mountAndLoad()
        expect(wrapper.find('#current-password').exists()).toBe(false)

        await wrapper.find('#new-password').setValue('newpass123')
        await wrapper.find('#confirm-password').setValue('newpass123')
        await wrapper.findAll('.stacked-form')[0].trigger('submit')
        await flushPromises()

        // Success banner visible; no error rendered.
        expect(wrapper.find('.form-success').exists()).toBe(true)
        expect(wrapper.find('[role="alert"]').exists()).toBe(false)
        // getMe was called only for the initial mount — no refetch.
        expect(getMe).toHaveBeenCalledTimes(1)
        // has_password flipped locally: the current-password field now renders.
        expect(wrapper.find('#current-password').exists()).toBe(true)
    })

    it('test_demo_role_shows_banner_and_disables_password_inputs', async () => {
        makeAuthMock('demo')

        const wrapper = await mountAndLoad()

        expect(wrapper.find('.demo-banner').exists()).toBe(true)
        expect(wrapper.find('.demo-banner').attributes('role')).toBe('alert')
        expect((wrapper.find('#new-password').element as HTMLInputElement).disabled).toBe(true)
        expect((wrapper.find('#confirm-password').element as HTMLInputElement).disabled).toBe(true)
        expect((wrapper.find('#current-password').element as HTMLInputElement).disabled).toBe(true)
    })
})

describe('AccountView — sign-in methods / Google unlink', () => {
    const ME_LINKED_WITH_PW = {
        id: 1,
        email: 'user@example.com',
        display_name: 'Test User',
        has_password: true,
        google_linked: true,
        role: 'member',
    }
    const ME_LINKED_NO_PW = {
        id: 1,
        email: 'user@example.com',
        display_name: null,
        has_password: false,
        google_linked: true,
        role: 'member',
    }
    const ME_NOT_LINKED = {
        id: 1,
        email: 'user@example.com',
        display_name: 'Test User',
        has_password: true,
        google_linked: false,
        role: 'member',
    }

    let mockPush: ReturnType<typeof vi.fn>
    let mockClearAuth: ReturnType<typeof vi.fn>

    beforeEach(() => {
        vi.resetAllMocks()
        mockPush = vi.fn()
        mockClearAuth = vi.fn()
        vi.mocked(useRouter).mockReturnValue({ push: mockPush } as never)
        vi.mocked(useAuth).mockReturnValue(
            baseMakeAuthMock({
                role: ref('member') as never,
                isAuthenticated: ref(true) as never,
                clearAuth: mockClearAuth as never,
            }) as never,
        )
        vi.mocked(useDateFormat).mockReturnValue(baseDateFormatMock() as never)
        vi.mocked(getMe).mockResolvedValue(ME_LINKED_WITH_PW)
        vi.mocked(getPreferences).mockResolvedValue({
            landing_page: 'dashboard' as const,
            date_format: 'iso' as const,
        } as never)
    })

    async function mountAndLoad() {
        const wrapper = mount(AccountView)
        await flushPromises()
        return wrapper
    }

    it('test_google_linked_no_password_shows_warning_no_unlink_button', async () => {
        vi.mocked(getMe).mockResolvedValue(ME_LINKED_NO_PW)
        const wrapper = await mountAndLoad()

        // Inline warning must be visible.
        const alerts = wrapper.findAll('[role="alert"]')
        const warning = alerts.find((w) => w.text().includes('Set a password first'))
        expect(warning).toBeTruthy()

        // Unlink button must not be rendered when there is no password.
        const unlinkBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Unlink Google')
        expect(unlinkBtn).toBeUndefined()
    })

    it('test_google_linked_with_password_shows_unlink_button_and_opens_dialog', async () => {
        const wrapper = await mountAndLoad()

        // "Unlink Google" button present.
        const unlinkBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Unlink Google')
        expect(unlinkBtn).toBeTruthy()

        // Clicking it opens the confirm dialog.
        await unlinkBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        const dialog = wrapper.findComponent(DeleteConfirmDialog)
        expect(dialog.props('visible')).toBe(true)
    })

    it('test_google_not_linked_shows_not_linked_no_button', async () => {
        vi.mocked(getMe).mockResolvedValue(ME_NOT_LINKED)
        const wrapper = await mountAndLoad()

        // Status text shows "Not linked".
        expect(wrapper.text()).toContain('Not linked')

        // No Unlink button rendered.
        const unlinkBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Unlink Google')
        expect(unlinkBtn).toBeUndefined()
    })

    it('test_confirm_unlink_success_calls_clearAuth_and_navigates', async () => {
        vi.mocked(unlinkGoogle).mockResolvedValueOnce({ message: 'Google account unlinked' })
        const wrapper = await mountAndLoad()

        // Open the dialog via the Unlink Google button.
        const unlinkBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Unlink Google')
        await unlinkBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Emit confirm on the dialog to trigger confirmUnlinkGoogle.
        await wrapper.findComponent(DeleteConfirmDialog).vm.$emit('confirm')
        await flushPromises()

        expect(mockClearAuth).toHaveBeenCalledOnce()
        expect(mockPush).toHaveBeenCalledWith('/')
    })

    it('test_confirm_unlink_failure_shows_inline_error_no_navigation', async () => {
        vi.mocked(unlinkGoogle).mockRejectedValueOnce(new Error('Server error'))
        const wrapper = await mountAndLoad()

        // Open dialog and confirm.
        const unlinkBtn = wrapper.findAll('button').find((b) => b.text().trim() === 'Unlink Google')
        await unlinkBtn!.trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.findComponent(DeleteConfirmDialog).vm.$emit('confirm')
        await flushPromises()

        // Inline error shown; no navigation.
        const alerts = wrapper.findAll('[role="alert"]')
        const errorAlert = alerts.find((w) => w.text().includes('Server error'))
        expect(errorAlert).toBeTruthy()
        expect(mockClearAuth).not.toHaveBeenCalled()
        expect(mockPush).not.toHaveBeenCalled()
    })
})
