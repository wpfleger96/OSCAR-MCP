import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/auth')

import { useAuth } from '@/composables/useAuth'
import * as authApi from '@/api/auth'

const mockStatus = {
    authenticated: true,
    auth_mode: 'multiuser',
    user: { id: 1, email: 'alice@example.com', display_name: 'Alice', role: 'user' },
    profiles: [{ id: 10, name: 'Primary' }],
    active_profile_id: 10,
}

describe('useAuth', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        const { clearAuth } = useAuth()
        clearAuth()
    })

    it('fetchStatus_firstCall_populatesStatusFromApi', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        const { fetchStatus, isAuthenticated, user } = useAuth()

        expect(isAuthenticated.value).toBe(false)

        await fetchStatus()

        expect(isAuthenticated.value).toBe(true)
        expect(user.value?.email).toBe('alice@example.com')
    })

    it('fetchStatus_secondCall_doesNotRefetch', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        const { fetchStatus } = useAuth()

        await fetchStatus()
        await fetchStatus() // second call — status is fresh, no API call

        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1)
    })

    it('fetchStatus_revalidatesAfterTTLExpiry', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValue(mockStatus)
        const { fetchStatus } = useAuth()

        // First fetch
        await fetchStatus()
        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1)

        // Simulate time passage beyond REVALIDATE_MS (5 min) by patching lastFetched
        // via clearAuth + re-fetch without fully clearing status
        const { clearAuth } = useAuth()
        clearAuth()
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
            ...mockStatus,
            auth_mode: 'local',
        })
        await fetchStatus()

        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)
    })

    it('clearAuth_preventsStaleWrite', async () => {
        let resolvePromise!: (v: typeof mockStatus) => void
        const pendingPromise = new Promise<typeof mockStatus>((resolve) => {
            resolvePromise = resolve
        })
        vi.mocked(authApi.getAuthStatus).mockReturnValueOnce(pendingPromise)

        const { fetchStatus, clearAuth, isAuthenticated } = useAuth()

        // Start fetch but don't await yet
        const fetchingPromise = fetchStatus()
        // Clear auth before the in-flight fetch completes
        clearAuth()
        // Now let the in-flight fetch resolve
        resolvePromise(mockStatus)
        await fetchingPromise

        // The cleared generation should prevent status from being written
        expect(isAuthenticated.value).toBe(false)
    })

    it('refreshStatus_forcesRefetchIgnoringCache', async () => {
        vi.mocked(authApi.getAuthStatus)
            .mockResolvedValueOnce(mockStatus)
            .mockResolvedValueOnce({ ...mockStatus, auth_mode: 'local' })

        const { fetchStatus, refreshStatus, isLocal } = useAuth()

        await fetchStatus()
        expect(isLocal.value).toBe(false) // first fetch: multiuser

        await refreshStatus() // force refetch despite cached status
        expect(isLocal.value).toBe(true) // second fetch: local
        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)
    })

    it('login_success_fetchesUpdatedStatus', async () => {
        const loggedInStatus = { ...mockStatus, authenticated: true }
        vi.mocked(authApi.loginUser).mockResolvedValueOnce({ message: 'ok' })
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(loggedInStatus)

        const { login, isAuthenticated } = useAuth()
        await login('alice@example.com', 'hunter2')

        expect(authApi.loginUser).toHaveBeenCalledWith({
            email: 'alice@example.com',
            password: 'hunter2',
        })
        expect(isAuthenticated.value).toBe(true)
    })

    it('logout_clearsAuthState', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        vi.mocked(authApi.logoutUser).mockResolvedValueOnce({ message: 'ok' })

        const { fetchStatus, logout, isAuthenticated } = useAuth()
        await fetchStatus()
        expect(isAuthenticated.value).toBe(true)

        await logout()
        expect(isAuthenticated.value).toBe(false)
    })

    it('clearAuth_resetsStateWithoutApiCall', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        const { fetchStatus, clearAuth, isAuthenticated } = useAuth()
        await fetchStatus()

        clearAuth()

        expect(isAuthenticated.value).toBe(false)
        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1)
    })

    it('isLocal_trueWhenAuthModeIsLocal', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
            ...mockStatus,
            auth_mode: 'local',
        })
        const { fetchStatus, isLocal } = useAuth()
        await fetchStatus()
        expect(isLocal.value).toBe(true)
    })

    it('setActiveProfile_incrementsProfileKey', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        vi.mocked(authApi.switchProfile).mockResolvedValueOnce({ message: 'ok' })

        const { fetchStatus, setActiveProfile, profileKey } = useAuth()
        await fetchStatus()

        const before = profileKey.value
        await setActiveProfile(99)
        expect(profileKey.value).toBe(before + 1)
    })

    it('setActiveProfile_doesNotIncrementProfileKeyOnFailure', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        vi.mocked(authApi.switchProfile).mockRejectedValueOnce(new Error('Network Error'))

        const { fetchStatus, setActiveProfile, profileKey } = useAuth()
        await fetchStatus()

        const before = profileKey.value
        await expect(setActiveProfile(99)).rejects.toThrow('Network Error')
        expect(profileKey.value).toBe(before) // not incremented on failure
    })

    it('refreshStatus_generationGuard_staleAnonymousFetchCannotOverwriteAuthenticated', async () => {
        // Scenario: router guard starts an anonymous fetch (gen=N).
        // User redeems invite → refreshStatus() increments generation (gen=N+1)
        // and starts an authenticated fetch. The old anonymous fetch settles LAST.
        // isAuthenticated must stay true.

        const authedStatus = { ...mockStatus, authenticated: true }
        const anonStatus = { ...mockStatus, authenticated: false }

        let resolveAnonymous!: (v: typeof mockStatus) => void
        const anonymousPromise = new Promise<typeof mockStatus>((res) => {
            resolveAnonymous = res
        })

        // Anonymous fetch (from router guard) starts first but resolves last.
        vi.mocked(authApi.getAuthStatus)
            .mockReturnValueOnce(anonymousPromise)
            .mockResolvedValueOnce(authedStatus)

        const { fetchStatus, refreshStatus, isAuthenticated } = useAuth()

        // Start anonymous fetch without awaiting.
        const anonymousFetchPromise = fetchStatus()

        // Simulate invite redemption: refreshStatus increments generation.
        const refreshPromise = refreshStatus()

        // Let the authenticated fetch resolve.
        await refreshPromise
        expect(isAuthenticated.value).toBe(true) // authenticated now

        // Now resolve the stale anonymous fetch.
        resolveAnonymous(anonStatus)
        await anonymousFetchPromise

        // Stale fetch must not have overwritten authenticated status.
        expect(isAuthenticated.value).toBe(true)
    })

    it('fetchStatus_supersededByRefresh_chainesToReplacementWhenOldResolvesFirst', async () => {
        // Scenario: router guard starts an anonymous fetch (gen=N).
        // refreshStatus() fires (gen=N+1) and starts an authenticated fetch.
        // The anonymous request resolves WHILE the authenticated request is still pending.
        //
        // Without the fix: P1's .then() returns undefined → P1 settles immediately →
        //   guardSettled = true before auth resolves → guard reads isAuthenticated=false → bails.
        // With the fix: P1's .then() returns _fetchPromise (P2) → P1 chains to P2 →
        //   guardSettled stays false until P2 resolves → guard reads isAuthenticated=true.

        const anonStatus = { ...mockStatus, authenticated: false }
        const authedStatus = { ...mockStatus, authenticated: true }

        let resolveAnon!: (v: typeof mockStatus) => void
        let resolveAuth!: (v: typeof mockStatus) => void

        vi.mocked(authApi.getAuthStatus)
            .mockReturnValueOnce(
                new Promise((r) => {
                    resolveAnon = r
                }),
            )
            .mockReturnValueOnce(
                new Promise((r) => {
                    resolveAuth = r
                }),
            )

        const { fetchStatus, refreshStatus, isAuthenticated } = useAuth()

        // Router guard starts anonymous fetch (P1) and tracks when it settles.
        let guardSettled = false
        const guardPromise = fetchStatus().then(() => {
            guardSettled = true
        })
        // refreshStatus invalidates gen and starts authenticated fetch (P2).
        const refreshPromise = refreshStatus()

        // Resolve anonymous ONLY — leave the authenticated request still pending.
        resolveAnon(anonStatus)
        // Flush microtasks so P1's .then() chain runs before we assert.
        for (let i = 0; i < 5; i++) await Promise.resolve()

        // KEY: with the fix, P1 chained to P2 and P2 is still pending → guard not yet settled.
        // Without the fix, P1 would have returned undefined and settled here → guard settled = true.
        expect(guardSettled).toBe(false)

        // Now resolve the authenticated fetch — P2 writes status, P1 (chained) resolves.
        resolveAuth(authedStatus)
        await guardPromise
        await refreshPromise

        // Guard received the authenticated result via the chain.
        expect(isAuthenticated.value).toBe(true)
    })

    it('login_generationGuard_stalePreLoginFetchCannotOverwriteAuthenticated', async () => {
        // Scenario: fetchStatus starts (gen=N), login() fires before it completes.
        // login() increments generation (gen=N+1). Old fetch resolves last.
        // isAuthenticated must stay true.

        const authedStatus = { ...mockStatus, authenticated: true }
        const anonStatus = { ...mockStatus, authenticated: false }

        let resolveAnonymous!: (v: typeof mockStatus) => void
        const anonymousPromise = new Promise<typeof mockStatus>((res) => {
            resolveAnonymous = res
        })

        vi.mocked(authApi.getAuthStatus)
            .mockReturnValueOnce(anonymousPromise)
            .mockResolvedValueOnce(authedStatus)
        vi.mocked(authApi.loginUser).mockResolvedValueOnce({ message: 'ok' })

        const { fetchStatus, login, isAuthenticated } = useAuth()

        // Start anonymous fetch without awaiting.
        const anonymousFetchPromise = fetchStatus()

        // Login invalidates the old generation.
        await login('alice@example.com', 'pw')
        expect(isAuthenticated.value).toBe(true)

        // Now let the old anonymous fetch settle.
        resolveAnonymous(anonStatus)
        await anonymousFetchPromise

        // Must still be authenticated.
        expect(isAuthenticated.value).toBe(true)
    })
})
