import { computed, ref } from 'vue'
import type { AuthStatusResponse } from '@/types'
import { demoLoginUser, getAuthStatus, loginUser, logoutUser, switchProfile } from '@/api/auth'

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
    const role = computed(() => status.value?.user?.role ?? null)
    // canWrite: local mode always allows writes; multiuser blocks the demo role.
    const canWrite = computed(
        () => isLocal.value || (status.value?.user?.role !== 'demo' && isAuthenticated.value),
    )
    const demoAvailable = computed(() => status.value?.demo_available ?? false)

    async function fetchStatus(): Promise<void> {
        const now = Date.now()
        if (status.value !== null && now - _lastFetched < REVALIDATE_MS) return
        if (_fetchPromise !== null) return _fetchPromise
        const gen = _generation
        _fetchPromise = getAuthStatus(AbortSignal.timeout(10_000))
            .then((s) => {
                if (_generation === gen) {
                    // Only write if this generation is still the active one.
                    status.value = s
                    _lastFetched = Date.now()
                } else if (_fetchPromise !== null) {
                    // Superseded by a newer generation — chain to the active fetch
                    // so callers awaiting this promise get the authenticated result.
                    return _fetchPromise
                }
            })
            .finally(() => {
                // Only clear _fetchPromise if this generation owns it —
                // prevents a stale .finally() from killing a newer in-flight request.
                if (_generation === gen) {
                    _fetchPromise = null
                }
            })
        return _fetchPromise
    }

    async function refreshStatus(): Promise<void> {
        status.value = null
        _fetchPromise = null
        _generation++ // invalidate any in-flight requests from earlier generation
        _lastFetched = 0
        await fetchStatus()
    }

    async function login(email: string, password: string): Promise<void> {
        await loginUser({ email, password })
        await refreshStatus()
    }

    async function demoLogin(): Promise<void> {
        await demoLoginUser()
        await refreshStatus()
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
        role,
        canWrite,
        demoAvailable,
        profileKey,
        fetchStatus,
        refreshStatus,
        login,
        demoLogin,
        logout,
        clearAuth,
        setActiveProfile,
    }
}
