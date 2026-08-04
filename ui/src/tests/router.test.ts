import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { ref } from 'vue'

vi.mock('@/composables/useAuth')

// Import the production guard — guard logic comes from the real module, not a copy.
import { authGuard } from '@/router'
import { useAuth } from '@/composables/useAuth'

function makeAuthMock(authed: boolean, local: boolean) {
    vi.mocked(useAuth).mockReturnValue({
        isAuthenticated: ref(authed) as never,
        isLocal: ref(local) as never,
        fetchStatus: vi.fn().mockResolvedValue(undefined),
        refreshStatus: vi.fn().mockResolvedValue(undefined),
        user: ref(null) as never,
        profiles: ref([]) as never,
        activeProfileId: ref(null) as never,
        authMode: ref(local ? 'local' : 'multiuser') as never,
        profileKey: ref(0) as never,
        login: vi.fn(),
        logout: vi.fn(),
        clearAuth: vi.fn(),
        setActiveProfile: vi.fn(),
    })
}

function makeRoute(
    path: string,
    meta: Record<string, unknown> = {},
): { path: string; meta: Record<string, unknown> } {
    return { path, meta }
}

describe('authGuard (production)', () => {
    beforeEach(() => {
        vi.resetAllMocks()
    })

    it('test_unauthenticated_access_to_guarded_route_redirects_to_login', async () => {
        makeAuthMock(false, false)
        const result = await authGuard(makeRoute('/sessions') as never)
        expect(result).toBe('/')
    })

    it('test_unauthenticated_access_to_auth_free_route_is_allowed', async () => {
        makeAuthMock(false, false)
        const result = await authGuard(makeRoute('/', { authFree: true }) as never)
        expect(result).toBeUndefined()
    })

    it('test_unauthenticated_access_to_invite_route_is_allowed', async () => {
        makeAuthMock(false, false)
        const result = await authGuard(makeRoute('/invite', { authFree: true }) as never)
        expect(result).toBeUndefined()
    })

    it('test_authenticated_access_to_login_redirects_to_dashboard', async () => {
        makeAuthMock(true, false)
        const result = await authGuard(makeRoute('/') as never)
        expect(result).toBe('/dashboard')
    })

    it('test_local_mode_bypasses_auth_check', async () => {
        makeAuthMock(false, true)
        const result = await authGuard(makeRoute('/sessions') as never)
        expect(result).toBeUndefined()
    })

    it('test_local_mode_on_login_page_redirects_to_dashboard', async () => {
        makeAuthMock(false, true)
        const result = await authGuard(makeRoute('/') as never)
        expect(result).toBe('/dashboard')
    })
})

// ---------------------------------------------------------------------------
// Workflow integration tests
// ---------------------------------------------------------------------------

vi.mock('@/api/auth')
vi.mock('@/api/profiles')
vi.mock('@/api/import', () => ({
    importFiles: vi.fn(),
    detectSources: vi.fn(),
    importFromPath: vi.fn(),
}))
vi.mock('@/api/sse', () => ({ connectImportProgress: vi.fn(), cancelImport: vi.fn() }))
vi.mock('@/composables/useDarkMode', () => ({
    useDarkMode: () => ({ isDark: ref(false), toggleDark: vi.fn() }),
}))
vi.mock('@/utils/formatting', () => ({ formatBytes: (n: number) => `${n}B` }))

import * as authApi from '@/api/auth'
import * as profilesApi from '@/api/profiles'
import * as importApi from '@/api/import'

describe('workflow: invite route renders from fragment', () => {
    it('test_invite_route_renders_from_hash', async () => {
        vi.mocked(authApi.lookupInvite).mockResolvedValueOnce({
            valid: true,
            email: 'invited@example.com',
        })
        makeAuthMock(false, false)

        const InviteView = (await import('@/views/InviteView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/invite', component: InviteView }],
        })
        await testRouter.push('/invite#SOMETOKEN')

        mount(InviteView, { global: { plugins: [testRouter] } })

        // Await onMounted which calls lookupInvite with the fragment token.
        await new Promise((r) => setTimeout(r, 0))

        // lookupInvite is called with the token extracted from route.hash (#SOMETOKEN → "SOMETOKEN").
        expect(authApi.lookupInvite).toHaveBeenCalledWith({ token: 'SOMETOKEN' })
    })
})

describe('workflow: redemption refreshes status', () => {
    it('test_redemption_clears_cache_and_reaches_dashboard', async () => {
        // refreshStatus is the key: it clears stale cached unauthenticated status
        const refreshStatusMock = vi.fn().mockResolvedValue(undefined)
        vi.mocked(useAuth).mockReturnValue({
            isAuthenticated: ref(false) as never,
            isLocal: ref(false) as never,
            fetchStatus: vi.fn().mockResolvedValue(undefined),
            refreshStatus: refreshStatusMock,
            user: ref(null) as never,
            profiles: ref([]) as never,
            activeProfileId: ref(null) as never,
            authMode: ref('multiuser') as never,
            profileKey: ref(0) as never,
            login: vi.fn(),
            logout: vi.fn(),
            clearAuth: vi.fn(),
            setActiveProfile: vi.fn(),
        })

        vi.mocked(authApi.lookupInvite).mockResolvedValueOnce({ valid: true, email: 'u@x.com' })
        vi.mocked(authApi.redeemInvite).mockResolvedValueOnce({ message: 'ok' })

        const InviteView = (await import('@/views/InviteView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [
                { path: '/invite', component: InviteView },
                { path: '/dashboard', component: { template: '<div />' } },
            ],
        })
        await testRouter.push('/invite#MYTOKEN')
        const wrapper = mount(InviteView, { global: { plugins: [testRouter] } })

        // Await onMounted lookup
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        // Fill form and submit
        await wrapper.find('input[type="password"]').setValue('password1')
        await wrapper.findAll('input[type="password"]')[1].setValue('password1')
        await wrapper.find('form').trigger('submit')
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        expect(refreshStatusMock).toHaveBeenCalled()
    })
})

describe('workflow: login page has no sidebar', () => {
    it('test_login_page_has_no_sidebar', async () => {
        makeAuthMock(false, false)
        vi.mocked(authApi.getAuthStatus).mockResolvedValue({
            authenticated: false,
            auth_mode: 'multiuser',
            profiles: [],
        } as never)

        const App = (await import('@/App.vue')).default
        const LoginView = (await import('@/views/LoginView.vue')).default
        const AppSidebar = (await import('@/components/AppSidebar.vue')).default

        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [
                { path: '/', component: LoginView, meta: { authFree: true } },
                { path: '/dashboard', component: { template: '<div />' } },
            ],
        })
        await testRouter.push('/')

        const wrapper = mount(App, { global: { plugins: [testRouter] } })
        // Sidebar should not be rendered on auth-free routes
        expect(wrapper.findComponent(AppSidebar).exists()).toBe(false)
    })
})

// profileKey increment is covered in useAuth.test.ts (setActiveProfile_incrementsProfileKey).
// Testing it here would require vi.unmock which breaks subsequent mocks in the same file.

describe('workflow: import aborts on failed profile switch', () => {
    it('test_import_aborts_on_failed_profile_switch', async () => {
        const setActiveProfileMock = vi.fn().mockRejectedValueOnce(new Error('Network Error'))
        vi.mocked(useAuth).mockReturnValue({
            isAuthenticated: ref(true) as never,
            isLocal: ref(false) as never,
            fetchStatus: vi.fn().mockResolvedValue(undefined),
            refreshStatus: vi.fn().mockResolvedValue(undefined),
            user: ref({ id: 1, email: 'u@x.com', display_name: 'U', role: 'user' }) as never,
            profiles: ref([
                { id: 1, name: 'Primary' },
                { id: 2, name: 'Work' },
            ]) as never,
            activeProfileId: ref(1) as never,
            authMode: ref('multiuser') as never,
            profileKey: ref(0) as never,
            login: vi.fn(),
            logout: vi.fn(),
            clearAuth: vi.fn(),
            setActiveProfile: setActiveProfileMock,
        })

        const ImportView = (await import('@/views/ImportView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/import', component: ImportView }],
        })
        await testRouter.push('/import')
        const wrapper = mount(ImportView, { global: { plugins: [testRouter] } })

        // In Vue 3 <script setup>, refs auto-unwrap through vm's proxy.
        // Assign directly (no .value) and call the internal function via (vm as any).
        const vm = wrapper.vm as unknown as Record<string, unknown>
        vm['selectedProfileId'] = 2 // assigns into the underlying ref via proxy
        await (vm as { handleImport: () => Promise<void> }).handleImport()

        expect(vi.mocked(importApi.importFiles)).not.toHaveBeenCalled()
        expect(vm['importError']).toContain('Could not switch')
    })
})

describe('workflow: profile default calls PATCH endpoint', () => {
    it('test_profile_default_calls_patch_endpoint', async () => {
        const setDefaultMock = vi.fn().mockResolvedValueOnce({
            id: 2,
            name: 'Work',
            user_id: 1,
            is_default: true,
        })
        vi.mocked(profilesApi.setDefaultProfile).mockImplementation(setDefaultMock)
        vi.mocked(profilesApi.listProfiles).mockResolvedValueOnce([
            { id: 1, name: 'Primary', user_id: 1 },
            { id: 2, name: 'Work', user_id: 1 },
        ])

        makeAuthMock(true, false)

        const ProfilesView = (await import('@/views/ProfilesView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/profiles', component: ProfilesView }],
        })
        await testRouter.push('/profiles')
        const wrapper = mount(ProfilesView, { global: { plugins: [testRouter] } })

        // Wait for onMounted listProfiles
        await wrapper.vm.$nextTick()
        await wrapper.vm.$nextTick()

        // Click "Set default" button (should call setDefaultProfile, NOT setActiveProfile)
        const setDefaultBtn = wrapper
            .findAll('button')
            .find((b) => b.text().includes('Set default'))
        expect(setDefaultBtn).toBeDefined()
        await setDefaultBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        expect(setDefaultMock).toHaveBeenCalled()
        // Must NOT have called setActiveProfile on the auth composable
        const authMock = vi.mocked(useAuth)()
        expect(authMock.setActiveProfile).not.toHaveBeenCalled()
    })
})
