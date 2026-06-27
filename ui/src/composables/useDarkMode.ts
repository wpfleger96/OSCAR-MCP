import { ref, watchEffect } from 'vue'

const isDark = ref(false)

export function useDarkMode() {
    function toggleDark(): void {
        isDark.value = !isDark.value
    }

    watchEffect(() => {
        const html = document.documentElement
        if (isDark.value) {
            html.classList.add('dark')
        } else {
            html.classList.remove('dark')
        }
        try {
            localStorage.setItem('snore-dark-mode', isDark.value ? '1' : '0')
        } catch {
            // localStorage unavailable
        }
    })

    return { isDark, toggleDark }
}

export function initDarkMode(): void {
    try {
        isDark.value = localStorage.getItem('snore-dark-mode') === '1'
    } catch {
        isDark.value = false
    }
}
