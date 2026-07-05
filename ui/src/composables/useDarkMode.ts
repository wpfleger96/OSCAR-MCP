import { ref, watchEffect } from 'vue'

function readSavedDark(): boolean {
    try {
        return localStorage.getItem('snore-dark-mode') === '1'
    } catch {
        return false
    }
}

const isDark = ref(readSavedDark())

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
        // private browsing or sandboxed iframe
    }
})

export function useDarkMode() {
    function toggleDark(): void {
        isDark.value = !isDark.value
    }

    return { isDark, toggleDark }
}
