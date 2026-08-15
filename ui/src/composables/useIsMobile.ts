import { useMediaQuery } from '@vueuse/core'

// Module-level singleton — shared across all callers (same pattern as useDarkMode).
const isMobile = useMediaQuery('(max-width: 767.98px)')

export function useIsMobile() {
    return { isMobile }
}
