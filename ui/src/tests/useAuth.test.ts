import { beforeEach, describe, expect, it, vi } from 'vitest'

// Reset module-level singleton state between tests.
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
        // Reset the module-level singleton between tests.
        const { clearAuth } = useAuth()
        clearAuth()
    })

    it('fetchStatus_firstCall_populatesStatusFromApi', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        const { fetchStatus, isAuthenticated, user } = useAuth()

        expect(isAuthenticated.value).toBe(false) // not yet fetched

        await fetchStatus()

        expect(isAuthenticated.value).toBe(true)
        expect(user.value?.email).toBe('alice@example.com')
    })

    it('fetchStatus_secondCall_doesNotRefetch', async () => {
        vi.mocked(authApi.getAuthStatus).mockResolvedValueOnce(mockStatus)
        const { fetchStatus } = useAuth()

        await fetchStatus()
        await fetchStatus() // second call — should not hit API again

        expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1)
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
        // getAuthStatus was called once (during fetchStatus), not again after clearAuth.
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
})
