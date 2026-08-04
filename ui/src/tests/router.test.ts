import { describe, expect, it, vi, beforeEach } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

// We test the guard logic independently with a minimal router
// rather than importing the real router (which pulls in all views).

vi.mock('@/composables/useAuth')
import { useAuth } from '@/composables/useAuth'

function makeTestRouter(authed: boolean, local: boolean) {
    vi.mocked(useAuth).mockReturnValue({
        isAuthenticated: { value: authed } as never,
        isLocal: { value: local } as never,
        fetchStatus: vi.fn().mockResolvedValue(undefined),
        user: { value: null } as never,
        profiles: { value: [] } as never,
        activeProfileId: { value: null } as never,
        authMode: { value: local ? 'local' : 'multiuser' } as never,
        login: vi.fn(),
        logout: vi.fn(),
        clearAuth: vi.fn(),
        setActiveProfile: vi.fn(),
    })

    const router = createRouter({
        history: createWebHistory(),
        routes: [
            { path: '/', component: { template: '<div>login</div>' } },
            { path: '/dashboard', component: { template: '<div>dash</div>' } },
            { path: '/sessions', component: { template: '<div>sessions</div>' } },
            { path: '/invite/:token', component: { template: '<div>invite</div>' } },
        ],
    })

    const AUTH_FREE = ['/', '/invite']

    router.beforeEach(async (to) => {
        const { fetchStatus, isAuthenticated, isLocal } = useAuth()
        try {
            await fetchStatus()
        } catch {
            // ignore
        }
        const resolvedAuthed = isAuthenticated.value || isLocal.value
        if (
            !resolvedAuthed &&
            !AUTH_FREE.some((p) => to.path === p || to.path.startsWith('/invite/'))
        ) {
            return '/'
        }
        if (resolvedAuthed && to.path === '/') {
            return '/dashboard'
        }
    })

    return router
}

describe('router guard', () => {
    beforeEach(() => {
        vi.resetAllMocks()
    })

    it('test_unauthenticated_access_to_guarded_route_redirects_to_login', async () => {
        const router = makeTestRouter(false, false)
        await router.push('/sessions')
        expect(router.currentRoute.value.path).toBe('/')
    })

    it('test_unauthenticated_access_to_invite_route_is_allowed', async () => {
        const router = makeTestRouter(false, false)
        await router.push('/invite/abc123')
        expect(router.currentRoute.value.path).toBe('/invite/abc123')
    })

    it('test_authenticated_access_to_login_redirects_to_dashboard', async () => {
        const router = makeTestRouter(true, false)
        await router.push('/')
        expect(router.currentRoute.value.path).toBe('/dashboard')
    })

    it('test_local_mode_bypasses_auth_check', async () => {
        const router = makeTestRouter(false, true)
        await router.push('/sessions')
        expect(router.currentRoute.value.path).toBe('/sessions')
    })

    it('test_local_mode_on_login_page_redirects_to_dashboard', async () => {
        const router = makeTestRouter(false, true)
        await router.push('/')
        expect(router.currentRoute.value.path).toBe('/dashboard')
    })
})
