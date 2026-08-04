import { computed, ref } from 'vue'
import type { AuthStatusResponse } from '@/types'
import { getAuthStatus, loginUser, logoutUser, switchProfile } from '@/api/auth'

// Module-level singleton — shared across all callers (same pattern as useDarkMode).
const status = ref<AuthStatusResponse | null>(null)
const profileKey = ref(0)
let _fetchPromise: Promise<void> | null = null
let _lastFetched = 0
let _generation = 0

const REVALIDATE_MS = 5 * 60 * 1000

export function useAuth() {
    const user = computed(() => status.value?.user ?? null)
    const isAuthenticated = computed(() => status.value?.authenticated ?? false)
    const isLocal = computed(() => status.value?.auth_mode === 'local')
    const profiles = computed(() => status.value?.profiles ?? [])
    const activeProfileId = computed(() => status.value?.active_profile_id ?? null)
    const authMode = computed(() => status.value?.auth_mode ?? null)

    async function fetchStatus(): Promise<void> {
        const now = Date.now()
        if (status.value !== null && now - _lastFetched < REVALIDATE_MS) return
        if (_fetchPromise !== null) return _fetchPromise
        const gen = _generation
        _fetchPromise = getAuthStatus()
            .then((s) => {
                // Discard the result if clearAuth() was called while in-flight.
                if (_generation === gen) {
                    status.value = s
                    _lastFetched = Date.now()
                }
            })
            .finally(() => {
                _fetchPromise = null
            })
        return _fetchPromise
    }

    async function refreshStatus(): Promise<void> {
        status.value = null
        _fetchPromise = null
        _lastFetched = 0
        await fetchStatus()
    }

    async function login(email: string, password: string): Promise<void> {
        await loginUser({ email, password })
        status.value = null
        _lastFetched = 0
        await fetchStatus()
    }

    function clearAuth(): void {
        status.value = null
        _fetchPromise = null
        _lastFetched = 0
        _generation++
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
        profileKey.value++
    }

    return {
        user,
        isAuthenticated,
        isLocal,
        profiles,
        activeProfileId,
        authMode,
        profileKey,
        fetchStatus,
        refreshStatus,
        login,
        logout,
        clearAuth,
        setActiveProfile,
    }
}
