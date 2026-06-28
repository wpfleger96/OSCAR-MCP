import { ref, watchEffect } from 'vue'

const isDark = ref(false)

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
        // private browsing or sandboxed iframe — persist in-memory only
    }
})

export function useDarkMode() {
    function toggleDark(): void {
        isDark.value = !isDark.value
    }

    return { isDark, toggleDark }
}

export function initDarkMode(): void {
    try {
        const saved = localStorage.getItem('snore-dark-mode') === '1'
        isDark.value = saved
        if (saved) document.documentElement.classList.add('dark')
    } catch {
        isDark.value = false
    }
}
