import { ref } from 'vue'
import { useAuth } from '@/composables/useAuth'
import client from '@/api/client'
import type { components } from '@/types/generated'

type DateFormat = components['schemas']['UserPreferences']['date_format']
type UserPreferences = components['schemas']['UserPreferences']

// Module-level singleton — shared across all callers (same pattern as useAuth/useDarkMode).
const dateFormat = ref<DateFormat>('iso')
let _loaded = false

export function useDateFormat() {
    /**
     * Fetch and apply the user's date_format preference.
     * Skips the request in local mode or when not authenticated.
     * Fetches at most once per module lifetime; swallows all errors.
     */
    async function loadDateFormat(): Promise<void> {
        if (_loaded) return
        const { isAuthenticated, isLocal } = useAuth()
        if (!isAuthenticated.value || isLocal.value) return
        _loaded = true
        try {
            const { data } = await client.get<UserPreferences>('/auth/me/preferences')
            dateFormat.value = data.date_format
        } catch {
            // Preference fetch failed — iso default remains in effect.
        }
    }

    /**
     * Directly update the date format — used after a successful preference save so that
     * all open views reflect the new format immediately without a reload.
     */
    function setDateFormat(fmt: DateFormat): void {
        dateFormat.value = fmt
        _loaded = true
    }

    /**
     * Format a date string or Date object using the loaded preference.
     *
     * iso    → YYYY-MM-DD (local calendar date, not UTC)
     * locale → browser locale default
     * short  → locale with 2-digit year (e.g. "1/5/25")
     */
    function formatDate(d: string | Date): string {
        const date = typeof d === 'string' ? new Date(d) : d
        switch (dateFormat.value) {
            case 'locale':
                return date.toLocaleDateString()
            case 'short':
                return date.toLocaleDateString(undefined, {
                    year: '2-digit',
                    month: 'numeric',
                    day: 'numeric',
                })
            default: {
                // 'iso'
                const y = date.getFullYear()
                const mo = String(date.getMonth() + 1).padStart(2, '0')
                const dy = String(date.getDate()).padStart(2, '0')
                return `${y}-${mo}-${dy}`
            }
        }
    }

    return { dateFormat, loadDateFormat, setDateFormat, formatDate }
}
