import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'

vi.mock('@/composables/useAuth')
vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button type="submit"><slot /></button>' },
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
            login: vi.fn(),
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

    it('test_demo_button_is_disabled', () => {
        const wrapper = mount(LoginView, {
            global: { plugins: [makeRouter()] },
        })
        const demoBtn = wrapper.find('.demo-btn')
        expect(demoBtn.attributes('disabled')).toBeDefined()
    })
})
