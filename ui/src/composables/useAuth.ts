import { computed, ref } from 'vue'
import type { AuthStatusResponse } from '@/types'
import { getAuthStatus, loginUser, logoutUser, switchProfile } from '@/api/auth'

// Module-level singleton — shared across all callers (same pattern as useDarkMode).
const status = ref<AuthStatusResponse | null>(null)
let _fetchPromise: Promise<void> | null = null

export function useAuth() {
    const user = computed(() => status.value?.user ?? null)
    const isAuthenticated = computed(() => status.value?.authenticated ?? false)
    const isLocal = computed(() => status.value?.auth_mode === 'local')
    const profiles = computed(() => status.value?.profiles ?? [])
    const activeProfileId = computed(() => status.value?.active_profile_id ?? null)
    const authMode = computed(() => status.value?.auth_mode ?? null)

    async function fetchStatus(): Promise<void> {
        if (status.value !== null) return
        if (_fetchPromise !== null) return _fetchPromise
        _fetchPromise = getAuthStatus()
            .then((s) => {
                status.value = s
            })
            .finally(() => {
                _fetchPromise = null
            })
        return _fetchPromise
    }

    async function login(email: string, password: string): Promise<void> {
        await loginUser({ email, password })
        status.value = null
        await fetchStatus()
    }

    function clearAuth(): void {
        status.value = null
        _fetchPromise = null
    }

    async function logout(): Promise<void> {
        try {
            await logoutUser()
        } finally {
            clearAuth()
        }
    }

    async function setActiveProfile(profileId: number): Promise<void> {
        await switchProfile({ profile_id: profileId })
        if (status.value) {
            status.value = { ...status.value, active_profile_id: profileId }
        }
    }

    return {
        user,
        isAuthenticated,
        isLocal,
        profiles,
        activeProfileId,
        authMode,
        fetchStatus,
        login,
        logout,
        clearAuth,
        setActiveProfile,
    }
}
