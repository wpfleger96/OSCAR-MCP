import { ref } from 'vue'
import { vi } from 'vitest'

/** Returns a complete useAuth mock object with sensible defaults.
 *  Pass field overrides to customise per-test (e.g. isAuthenticated, role, user). */
export function makeAuthMock(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return {
        isAuthenticated: ref(false),
        isLocal: ref(false),
        role: ref(null),
        user: ref(null),
        profiles: ref([]),
        activeProfileId: ref(null),
        authMode: ref('multiuser'),
        canWrite: ref(true),
        demoAvailable: ref(false),
        statusUnknown: ref(false),
        profileKey: ref(0),
        fetchStatus: vi.fn().mockResolvedValue(undefined),
        refreshStatus: vi.fn().mockResolvedValue(undefined),
        login: vi.fn(),
        demoLogin: vi.fn(),
        logout: vi.fn(),
        clearAuth: vi.fn(),
        setActiveProfile: vi.fn(),
        ...overrides,
    }
}

/** Returns a complete useDateFormat mock object. */
export function makeDateFormatMock(): Record<string, unknown> {
    return {
        formatDate: (d: string | Date) => String(d),
        loadDateFormat: vi.fn().mockResolvedValue(undefined),
        setDateFormat: vi.fn(),
        dateFormat: ref('iso'),
    }
}
