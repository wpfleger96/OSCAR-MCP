import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'

vi.mock('@/composables/useAuth')
vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button type="submit"><slot /></button>' },
}))
// LoginView pulls resolveLandingPath from the real router module; mock it so
// tests don't instantiate the app router or hit the preferences endpoint.
vi.mock('@/router', () => ({
    resolveLandingPath: vi.fn().mockResolvedValue('/dashboard'),
    default: {},
}))

import LoginView from '@/views/LoginView.vue'
import { useAuth } from '@/composables/useAuth'

function makeRouter() {
    return createRouter({
        history: createWebHistory(),
        routes: [
            { path: '/', component: LoginView },
            { path: '/dashboard', component: { template: '<div />' } },
        ],
    })
}

describe('LoginView', () => {
    beforeEach(() => {
        vi.mocked(useAuth).mockReturnValue({
            login: vi.fn().mockResolvedValue({ totpRequired: false }),
            submitTotp: vi.fn().mockResolvedValue(undefined),
            clearTotpChallenge: vi.fn(),
            demoLogin: vi.fn(),
            logout: vi.fn(),
            clearAuth: vi.fn(),
            fetchStatus: vi.fn(),
            refreshStatus: vi.fn(),
            setActiveProfile: vi.fn(),
            user: { value: null } as never,
            isAuthenticated: { value: false } as never,
            isLocal: { value: false } as never,
            profiles: { value: [] } as never,
            activeProfileId: { value: null } as never,
            authMode: { value: null } as never,
            role: { value: null } as never,
            canWrite: { value: true } as never,
            demoAvailable: { value: true } as never,
            totpEnrollmentRequired: { value: false } as never,
            statusUnknown: { value: false } as never,
            profileKey: { value: 0 } as never,
        })
    })

    it('test_renders_login_form', async () => {
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })
        expect(wrapper.find('input[type="email"]').exists()).toBe(true)
        expect(wrapper.find('input[type="password"]').exists()).toBe(true)
        expect(wrapper.find('[href="/api/v1/auth/google/login"]').exists()).toBe(true)
    })

    it('test_invalid_credentials_shows_401_error', async () => {
        const loginMock = vi.fn().mockRejectedValueOnce({
            response: { status: 401 },
            message: 'Unauthorized',
        })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        await wrapper.find('input[type="email"]').setValue('bad@example.com')
        await wrapper.find('input[type="password"]').setValue('wrong')
        await wrapper.find('form').trigger('submit')
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        expect(wrapper.find('[role="alert"]').text()).toBe('Invalid email or password')
    })

    it('test_rate_limit_shows_429_error', async () => {
        const loginMock = vi.fn().mockRejectedValueOnce({
            response: { status: 429 },
            message: 'Too Many Requests',
        })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        await wrapper.find('input[type="email"]').setValue('user@example.com')
        await wrapper.find('input[type="password"]').setValue('pw')
        await wrapper.find('form').trigger('submit')
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        expect(wrapper.find('[role="alert"]').text()).toContain('Too many attempts')
    })

    it('test_demo_button_is_enabled', () => {
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })
        const demoBtn = wrapper.find('.demo-btn')
        expect(demoBtn.attributes('disabled')).toBeUndefined()
    })

    it('test_hides_demo_button_when_demo_unavailable', () => {
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            demoAvailable: ref(false) as never,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })
        expect(wrapper.find('.demo-btn').exists()).toBe(false)
    })

    it('test_demo_button_click_calls_demoLogin_and_navigates_to_dashboard', async () => {
        const demoLoginMock = vi.fn().mockResolvedValueOnce(undefined)
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            demoLogin: demoLoginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const router = makeRouter()
        const wrapper = mount(LoginView, {
            global: { plugins: [router] },
        })

        await wrapper.find('.demo-btn').trigger('click')
        // flushPromises drains all microtasks (the async demoLogin + router.push).
        await flushPromises()

        expect(demoLoginMock).toHaveBeenCalledOnce()
        expect(router.currentRoute.value.path).toBe('/dashboard')
    })

    it('test_demo_button_shows_unavailable_on_404', async () => {
        const demoLoginMock = vi.fn().mockRejectedValueOnce({
            response: { status: 404 },
            message: 'Not Found',
        })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            demoLogin: demoLoginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        await wrapper.find('.demo-btn').trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        expect(wrapper.find('[role="alert"]').text()).toBe('Demo unavailable')
    })

    it('test_totp_required_login_transitions_to_code_step', async () => {
        const loginMock = vi.fn().mockResolvedValueOnce({ totpRequired: true })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        await wrapper.find('input[type="email"]').setValue('alice@example.com')
        await wrapper.find('input[type="password"]').setValue('hunter2')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
        expect(wrapper.find('input[type="email"]').exists()).toBe(false)
    })

    it('test_totp_submit_calls_submitTotp_and_navigates', async () => {
        const loginMock = vi.fn().mockResolvedValueOnce({ totpRequired: true })
        const submitTotpMock = vi.fn().mockResolvedValueOnce(undefined)
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: submitTotpMock,
            clearTotpChallenge: vi.fn(),
        })
        const router = makeRouter()
        const wrapper = mount(LoginView, {
            global: { plugins: [router] },
        })

        // Advance to TOTP step
        await wrapper.find('input[type="email"]').setValue('alice@example.com')
        await wrapper.find('input[type="password"]').setValue('hunter2')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        // Submit the code
        await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(submitTotpMock).toHaveBeenCalledWith('123456')
        expect(router.currentRoute.value.path).toBe('/dashboard')
    })

    it('test_totp_401_shows_invalid_code_error', async () => {
        const loginMock = vi.fn().mockResolvedValueOnce({ totpRequired: true })
        const submitTotpMock = vi.fn().mockRejectedValueOnce({
            response: { status: 401 },
            message: 'Unauthorized',
        })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: submitTotpMock,
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        await wrapper.find('input[type="email"]').setValue('alice@example.com')
        await wrapper.find('input[type="password"]').setValue('hunter2')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        await wrapper.find('input[autocomplete="one-time-code"]').setValue('000000')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(wrapper.find('[role="alert"]').text()).toBe('Invalid code — try again')
    })

    it('test_totp_429_shows_rate_limit_error', async () => {
        const loginMock = vi.fn().mockResolvedValueOnce({ totpRequired: true })
        const submitTotpMock = vi.fn().mockRejectedValueOnce({
            response: { status: 429 },
            message: 'Too Many Requests',
        })
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: submitTotpMock,
            clearTotpChallenge: vi.fn(),
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        // Advance to TOTP step
        await wrapper.find('input[type="email"]').setValue('alice@example.com')
        await wrapper.find('input[type="password"]').setValue('hunter2')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        // Submit the code — server responds with 429
        await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(wrapper.find('[role="alert"]').text()).toContain('Too many attempts')
    })

    it('test_totp_back_button_returns_to_password_step', async () => {
        const loginMock = vi.fn().mockResolvedValueOnce({ totpRequired: true })
        const clearTotpChallengeMock = vi.fn()
        vi.mocked(useAuth).mockReturnValue({
            ...(vi.mocked(useAuth)() as ReturnType<typeof useAuth>),
            login: loginMock,
            submitTotp: vi.fn(),
            clearTotpChallenge: clearTotpChallengeMock,
        })
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })

        // Advance to TOTP step
        await wrapper.find('input[type="email"]').setValue('alice@example.com')
        await wrapper.find('input[type="password"]').setValue('hunter2')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)

        // Click back
        await wrapper.find('.demo-btn').trigger('click')
        await wrapper.vm.$nextTick()

        expect(clearTotpChallengeMock).toHaveBeenCalledOnce()
        expect(wrapper.find('input[type="email"]').exists()).toBe(true)
        expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
    })
})
