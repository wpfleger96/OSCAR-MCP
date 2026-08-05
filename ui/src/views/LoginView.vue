<template>
    <div class="login-page">
        <div class="login-card">
            <h1 class="login-title">SNORE</h1>
            <p class="login-subtitle">Sleep data, your way.</p>

            <form class="login-form" @submit.prevent="handleLogin">
                <div class="field-group">
                    <label for="email" class="field-label">Email</label>
                    <input
                        id="email"
                        v-model="email"
                        type="email"
                        autocomplete="email"
                        required
                        class="field-input"
                        :disabled="loading"
                    />
                </div>
                <div class="field-group">
                    <label for="password" class="field-label">Password</label>
                    <input
                        id="password"
                        v-model="password"
                        type="password"
                        autocomplete="current-password"
                        required
                        class="field-input"
                        :disabled="loading"
                    />
                </div>

                <p v-if="errorMessage" role="alert" class="login-error">{{ errorMessage }}</p>

                <Button type="submit" class="login-btn" :disabled="loading">
                    <Loader2 v-if="loading" class="h-4 w-4 animate-spin mr-2" />
                    {{ loading ? 'Signing in…' : 'Sign in' }}
                </Button>
            </form>

            <div class="login-divider"><span>or</span></div>

            <a href="/api/v1/auth/google/login" class="google-btn">
                <svg class="google-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path
                        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                        fill="#4285F4"
                    />
                    <path
                        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                        fill="#34A853"
                    />
                    <path
                        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                        fill="#FBBC05"
                    />
                    <path
                        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                        fill="#EA4335"
                    />
                </svg>
                Sign in with Google
            </a>

            <p v-if="demoError" role="alert" class="login-error">{{ demoError }}</p>
            <button class="demo-btn" :disabled="demoLoading" @click="handleDemoLogin">
                <Loader2 v-if="demoLoading" class="h-4 w-4 animate-spin mr-2" />
                {{ demoLoading ? 'Signing in…' : 'Sign in as Demo' }}
            </button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const { login, demoLogin } = useAuth()

const email = ref('')
const password = ref('')
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const demoLoading = ref(false)
const demoError = ref<string | null>(null)

async function handleLogin() {
    errorMessage.value = null
    loading.value = true
    try {
        await login(email.value, password.value)
        router.push('/dashboard')
    } catch (e: unknown) {
        const status = (e as { response?: { status?: number } }).response?.status
        if (status === 401) {
            errorMessage.value = 'Invalid email or password'
        } else if (status === 429) {
            errorMessage.value = 'Too many attempts — try again later'
        } else if (!navigator.onLine || (e instanceof Error && e.message === 'Network Error')) {
            errorMessage.value = 'Unable to reach server'
        } else {
            errorMessage.value = e instanceof Error ? e.message : 'Sign-in failed'
        }
    } finally {
        loading.value = false
    }
}

async function handleDemoLogin() {
    demoError.value = null
    demoLoading.value = true
    try {
        await demoLogin()
        router.push('/dashboard')
    } catch (e: unknown) {
        const status = (e as { response?: { status?: number } }).response?.status
        if (status === 404) {
            demoError.value = 'Demo unavailable'
        } else if (!navigator.onLine || (e instanceof Error && e.message === 'Network Error')) {
            demoError.value = 'Unable to reach server'
        } else {
            demoError.value = 'Demo sign-in failed'
        }
    } finally {
        demoLoading.value = false
    }
}
</script>

<style scoped>
.login-page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-background);
    padding: 1rem;
}

.login-card {
    width: 100%;
    max-width: 360px;
    background: var(--color-card);
    border: 1px solid var(--color-border);
    border-radius: 12px;
    padding: 2rem 2rem 1.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.login-title {
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--color-primary);
    text-align: center;
    margin: 0;
}

.login-subtitle {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    text-align: center;
    margin: 0 0 0.5rem;
}

.login-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.field-group {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}

.field-label {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--color-foreground);
}

.field-input {
    height: 2.25rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0 0.75rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    transition: border-color 0.15s;
    outline: none;
}

.field-input:focus {
    border-color: var(--color-primary);
}

.field-input:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.login-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin: 0;
}

.login-btn {
    width: 100%;
    margin-top: 0.25rem;
}

.login-divider {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    color: var(--color-muted-foreground);
    font-size: 0.8rem;
}

.login-divider::before,
.login-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--color-border);
}

.google-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    width: 100%;
    padding: 0.6rem 1rem;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    background: var(--color-card);
    color: var(--color-foreground);
    font-size: 0.875rem;
    font-weight: 500;
    text-decoration: none;
    transition: background 0.15s;
    cursor: pointer;
}

.google-btn:hover {
    background: var(--color-accent);
}

.google-icon {
    width: 1.125rem;
    height: 1.125rem;
    flex-shrink: 0;
}

.demo-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.25rem;
    width: 100%;
    padding: 0.6rem 1rem;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    background: transparent;
    color: var(--color-muted-foreground);
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s;
}

.demo-btn:hover:not(:disabled) {
    background: var(--color-accent);
}

.demo-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}
</style>
