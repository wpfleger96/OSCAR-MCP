// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/auth')

import { useAuth } from '@/composables/useAuth'
import * as authApi from '@/api/auth'

const mockLoginResponse = { message: 'Logged in', totp_required: false, pending_token: null }

const mockStatus = {
    authenticated: true,
    auth_mode: 'multiuser',
    user: {
        id: 1,
        email: 'alice@example.com',
        display_name: 'Alice',
        role: 'member',
        totp_enabled: false,
    },
    profiles: [{ id: 10, name: 'Primary' }],
    active_profile_id: 10,
    demo_available: false,
    totp_enrollment_required: false,
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
        vi.mocked(authApi.loginUser).mockResolvedValueOnce(mockLoginResponse)
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(loggedInStatus)

        const { login, isAuthenticated } = useAuth()
        const result = await login('alice@example.com', 'hunter2')

        expect(authApi.loginUser).toHaveBeenCalledWith({
            email: 'alice@example.com',
            password: 'hunter2',
        })
        expect(result).toEqual({ totpRequired: false })
        expect(isAuthenticated.value).toBe(true)
    })

    it('login_totpRequired_storesPendingTokenAndSkipsStatusRefresh', async () => {
        const totpLoginResponse = {
            message: null,
            totp_required: true,
            pending_token: 'tok-abc',
        }
        vi.mocked(authApi.loginUser).mockResolvedValueOnce(totpLoginResponse)

        const { login, isAuthenticated } = useAuth()
        const result = await login('alice@example.com', 'hunter2')

        expect(result).toEqual({ totpRequired: true })
        // Must not have tried to refresh status
        expect(authApi.getAuthStatus).not.toHaveBeenCalled()
        expect(isAuthenticated.value).toBe(false)
    })

    it('submitTotp_success_completesAuthAndRefreshesStatus', async () => {
        const loggedInStatus = { ...mockStatus, authenticated: true }
        // Set up a pending token via login
        vi.mocked(authApi.loginUser).mockResolvedValueOnce({
            message: null,
            totp_required: true,
            pending_token: 'tok-abc',
        })
        vi.mocked(authApi.submitTotpChallenge).mockResolvedValueOnce({ message: 'Logged in' })
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(loggedInStatus)

        const { login, submitTotp, isAuthenticated } = useAuth()
        await login('alice@example.com', 'hunter2')
        await submitTotp('123456')

        expect(authApi.submitTotpChallenge).toHaveBeenCalledWith({
            pending_token: 'tok-abc',
            code: '123456',
        })
        expect(isAuthenticated.value).toBe(true)
    })

    it('submitTotp_withNoPendingToken_throws', async () => {
        const { submitTotp } = useAuth()
        await expect(submitTotp('123456')).rejects.toThrow('No pending TOTP challenge')
        expect(authApi.submitTotpChallenge).not.toHaveBeenCalled()
    })

    it('submitTotp_failedAttemptPreservesPendingToken_secondAttemptSucceeds', async () => {
        vi.mocked(authApi.loginUser).mockResolvedValueOnce({
            message: null,
            totp_required: true,
            pending_token: 'tok-abc',
        })
        vi.mocked(authApi.submitTotpChallenge)
            .mockRejectedValueOnce({ response: { status: 401 }, message: 'Unauthorized' })
            .mockResolvedValueOnce({ message: 'Logged in' })
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
            ...mockStatus,
            authenticated: true,
        })

        const { login, submitTotp } = useAuth()
        await login('alice@example.com', 'hunter2')

        // First attempt fails — pending token must be preserved so the next attempt works.
        await expect(submitTotp('000000')).rejects.toBeDefined()

        // Second attempt succeeds with the same pending_token.
        await submitTotp('123456')

        expect(authApi.submitTotpChallenge).toHaveBeenCalledTimes(2)
        expect(authApi.submitTotpChallenge).toHaveBeenNthCalledWith(1, {
            pending_token: 'tok-abc',
            code: '000000',
        })
        expect(authApi.submitTotpChallenge).toHaveBeenNthCalledWith(2, {
            pending_token: 'tok-abc',
            code: '123456',
        })
    })

    it('clearTotpChallenge_clearsPendingToken_subsequentSubmitTotpThrows', async () => {
        vi.mocked(authApi.loginUser).mockResolvedValueOnce({
            message: null,
            totp_required: true,
            pending_token: 'tok-abc',
        })

        const { login, submitTotp, clearTotpChallenge } = useAuth()
        await login('alice@example.com', 'hunter2')
        clearTotpChallenge()

        await expect(submitTotp('123456')).rejects.toThrow('No pending TOTP challenge')
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

    it('fetchStatus_retriesOnce_succeeds_populatesStatus', async () => {
        // First attempt fails; second attempt (after 500ms backoff) succeeds.
        vi.useFakeTimers()
        try {
            vi.mocked(authApi.getAuthStatus)
                .mockRejectedValueOnce(new Error('Network'))
                .mockResolvedValueOnce(mockStatus)

            const { fetchStatus, isAuthenticated } = useAuth()
            const p = fetchStatus()
            await vi.advanceTimersByTimeAsync(600) // advance past 500ms retry delay
            await p

            expect(isAuthenticated.value).toBe(true)
            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)
        } finally {
            vi.useRealTimers()
        }
    })

    it('fetchStatus_bothAttemptsFail_schedulesSelfHeal', async () => {
        // Both attempts fail; status stays null; background self-heal fires and succeeds.
        vi.useFakeTimers()
        try {
            vi.mocked(authApi.getAuthStatus)
                .mockRejectedValueOnce(new Error('Network'))
                .mockRejectedValueOnce(new Error('Network'))
                .mockResolvedValueOnce(mockStatus)

            const { fetchStatus, isAuthenticated } = useAuth()
            const p = fetchStatus()
            await vi.advanceTimersByTimeAsync(600) // past 500ms retry delay
            await p

            // Both attempts failed — status still unknown.
            expect(isAuthenticated.value).toBe(false)
            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)

            // Self-heal fires after 3 seconds and recovers.
            await vi.advanceTimersByTimeAsync(3_500)

            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(3)
            expect(isAuthenticated.value).toBe(true)
        } finally {
            vi.useRealTimers()
        }
    })

    it('clearAuth_cancelsPendingSelfHeal_noAdditionalFetchFires', async () => {
        // Both attempts fail → self-heal timer is scheduled → clearAuth cancels the timer.
        vi.useFakeTimers()
        try {
            vi.mocked(authApi.getAuthStatus)
                .mockRejectedValueOnce(new Error('Network'))
                .mockRejectedValueOnce(new Error('Network'))

            const { fetchStatus, clearAuth } = useAuth()
            const p = fetchStatus()
            await vi.advanceTimersByTimeAsync(600) // past 500ms retry delay
            await p

            // Both attempts failed — self-heal is now scheduled.
            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)

            // clearAuth cancels the pending self-heal timer.
            clearAuth()

            // Advance well past the heal delay — no additional API call should fire.
            await vi.advanceTimersByTimeAsync(3_500)

            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)
        } finally {
            vi.useRealTimers()
        }
    })

    it('fetchStatus_generationSupersededDuringBackoff_abandonsStalRetry', async () => {
        // First attempt fails → during the 500ms backoff, refreshStatus() bumps generation →
        // the stale retry is abandoned by the generation guard; no third call fires.
        vi.useFakeTimers()
        try {
            vi.mocked(authApi.getAuthStatus)
                .mockRejectedValueOnce(new Error('Network')) // gen=N first attempt
                .mockResolvedValueOnce(mockStatus) // gen=N+1 fetch from refreshStatus

            const { fetchStatus, refreshStatus, isAuthenticated } = useAuth()

            const oldP = fetchStatus() // starts gen=N; first call fails immediately
            await vi.advanceTimersByTimeAsync(100) // 100ms into the 500ms backoff

            // Bump generation mid-backoff; this starts gen=N+1's fetch (2nd getAuthStatus call).
            await refreshStatus()
            expect(isAuthenticated.value).toBe(true)

            // Advance past the stale backoff — gen=N retry fires but generation guard discards it.
            await vi.advanceTimersByTimeAsync(500)
            await oldP // resolves (superseded error is caught internally)

            // Exactly 2 calls: gen=N first attempt + gen=N+1 fetch from refreshStatus.
            expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2)
        } finally {
            vi.useRealTimers()
        }
    })

    describe('canWrite', () => {
        it('canWrite_localMode_notAuthenticated_returnsFalse', async () => {
            // Regression: old code returned true for any local-mode status object,
            // even when the actor could not be resolved (user null, authenticated false).
            vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
                ...mockStatus,
                authenticated: false,
                auth_mode: 'local',
                user: null,
            })
            const { fetchStatus, canWrite } = useAuth()
            await fetchStatus()
            expect(canWrite.value).toBe(false)
        })

        it('canWrite_localMode_authenticatedAdmin_returnsTrue', async () => {
            vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
                ...mockStatus,
                authenticated: true,
                auth_mode: 'local',
                user: {
                    id: 1,
                    email: 'admin@example.com',
                    display_name: 'Admin',
                    role: 'admin',
                    totp_enabled: false,
                },
            })
            const { fetchStatus, canWrite } = useAuth()
            await fetchStatus()
            expect(canWrite.value).toBe(true)
        })

        it('canWrite_multiuser_authenticatedDemoRole_returnsFalse', async () => {
            vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce({
                ...mockStatus,
                user: { ...mockStatus.user, role: 'demo' },
            })
            const { fetchStatus, canWrite } = useAuth()
            await fetchStatus()
            expect(canWrite.value).toBe(false)
        })
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
        vi.mocked(authApi.loginUser).mockResolvedValueOnce(mockLoginResponse)

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
