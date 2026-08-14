<template>
    <div class="login-page">
        <div class="login-card">
            <h1 class="login-title">SNORE</h1>
            <p class="login-subtitle">Sleep data, your way.</p>

            <form v-if="step === 'password'" class="login-form" @submit.prevent="handleLogin">
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

            <template v-if="step === 'password'">
                <div class="login-divider"><span>or</span></div>

                <GoogleSignInButton href="/api/v1/auth/google/login" />

                <template v-if="demoAvailable">
                    <p v-if="demoError" role="alert" class="login-error">{{ demoError }}</p>
                    <button class="demo-btn" :disabled="demoLoading" @click="handleDemoLogin">
                        <Loader2 v-if="demoLoading" class="h-4 w-4 animate-spin mr-2" />
                        {{ demoLoading ? 'Signing in…' : 'Sign in as Demo' }}
                    </button>
                </template>
            </template>

            <form v-else class="login-form" @submit.prevent="handleTotpSubmit">
                <div class="field-group">
                    <label for="totp-code" class="field-label">Authentication code</label>
                    <input
                        id="totp-code"
                        v-model="totpCode"
                        type="text"
                        autocomplete="one-time-code"
                        inputmode="numeric"
                        autofocus
                        class="field-input"
                        :disabled="loading"
                    />
                    <p class="field-hint">A saved recovery code also works.</p>
                </div>

                <p v-if="errorMessage" role="alert" class="login-error">{{ errorMessage }}</p>

                <Button type="submit" class="login-btn" :disabled="loading">
                    <Loader2 v-if="loading" class="h-4 w-4 animate-spin mr-2" />
                    {{ loading ? 'Verifying…' : 'Verify' }}
                </Button>

                <button type="button" class="demo-btn" :disabled="loading" @click="handleTotpBack">
                    Back
                </button>
            </form>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import GoogleSignInButton from '@/components/GoogleSignInButton.vue'
import { useAuth } from '@/composables/useAuth'
import { resolveLandingPath } from '@/router'

const router = useRouter()
const { login, submitTotp, clearTotpChallenge, demoLogin, demoAvailable, isAuthenticated } =
    useAuth()

const step = ref<'password' | 'totp'>('password')
const email = ref('')
const password = ref('')
const totpCode = ref('')
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const demoLoading = ref(false)
const demoError = ref<string | null>(null)

// When background self-heal authenticates the session while the user sits on the login
// page, navigate to their landing path. Skip if an explicit login handler is already
// navigating — it owns navigation in that case (guarded by the loading flags it owns).
watch(isAuthenticated, async (authed) => {
    if (!authed) return
    if (loading.value || demoLoading.value) return
    router.replace(await resolveLandingPath())
})

async function handleLogin() {
    errorMessage.value = null
    loading.value = true
    try {
        const { totpRequired } = await login(email.value, password.value)
        if (totpRequired) {
            step.value = 'totp'
            return
        }
        router.push(await resolveLandingPath())
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

async function handleTotpSubmit() {
    errorMessage.value = null
    loading.value = true
    try {
        await submitTotp(totpCode.value)
        router.push(await resolveLandingPath())
    } catch (e: unknown) {
        const status = (e as { response?: { status?: number } }).response?.status
        if (status === 401) {
            errorMessage.value = 'Invalid code — try again'
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

function handleTotpBack() {
    clearTotpChallenge()
    step.value = 'password'
    errorMessage.value = null
    totpCode.value = ''
}

async function handleDemoLogin() {
    demoError.value = null
    demoLoading.value = true
    try {
        await demoLogin()
        router.push(await resolveLandingPath())
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
.login-subtitle {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    text-align: center;
    margin: 0 0 0.5rem;
}

.field-hint {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
    margin: 0.25rem 0 0;
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
