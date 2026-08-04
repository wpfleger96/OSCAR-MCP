<template>
    <div class="login-page">
        <div class="login-card">
            <h1 class="login-title">SNORE</h1>

            <template v-if="lookupState === 'loading'">
                <div class="loading-row">
                    <Loader2 class="h-5 w-5 animate-spin text-muted-foreground" />
                    <span class="loading-text">Checking invite…</span>
                </div>
            </template>

            <template v-else-if="lookupState === 'invalid'">
                <p class="invite-error">{{ lookupError }}</p>
            </template>

            <template v-else-if="lookupState === 'valid'">
                <p class="invite-greeting">
                    You've been invited. Set a password to complete your account.
                </p>

                <form class="login-form" @submit.prevent="handleRedeem">
                    <div class="field-group">
                        <label for="invite-email" class="field-label">Email</label>
                        <input
                            id="invite-email"
                            :value="inviteEmail"
                            type="email"
                            readonly
                            class="field-input field-input--readonly"
                        />
                    </div>
                    <div class="field-group">
                        <label for="invite-password" class="field-label">Password</label>
                        <input
                            id="invite-password"
                            v-model="password"
                            type="password"
                            autocomplete="new-password"
                            required
                            class="field-input"
                            :disabled="redeemLoading"
                        />
                    </div>
                    <div class="field-group">
                        <label for="invite-confirm" class="field-label">Confirm password</label>
                        <input
                            id="invite-confirm"
                            v-model="confirmPassword"
                            type="password"
                            autocomplete="new-password"
                            required
                            class="field-input"
                            :disabled="redeemLoading"
                        />
                    </div>

                    <p v-if="redeemError" role="alert" class="login-error">{{ redeemError }}</p>

                    <Button type="submit" class="login-btn" :disabled="redeemLoading">
                        <Loader2 v-if="redeemLoading" class="h-4 w-4 animate-spin mr-2" />
                        {{ redeemLoading ? 'Creating account…' : 'Set password' }}
                    </Button>
                </form>

                <div class="login-divider"><span>or</span></div>

                <button
                    type="button"
                    class="google-btn"
                    :disabled="googleLoading"
                    @click="startGoogleSignup"
                >
                    <Loader2 v-if="googleLoading" class="h-4 w-4 animate-spin" />
                    <template v-else>
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
                    </template>
                    Sign up with Google
                </button>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { lookupInvite, redeemInvite, initiateGoogleInvite } from '@/api/auth'
import { useAuth } from '@/composables/useAuth'

const route = useRoute()
const router = useRouter()
const { refreshStatus } = useAuth()

// Token lives in the URL fragment (#<token>) so it never enters access logs.
const token = computed(() => (route.hash ? route.hash.slice(1) : ''))

type LookupState = 'loading' | 'valid' | 'invalid'
const lookupState = ref<LookupState>('loading')
const lookupError = ref('This invite link is invalid or has expired.')
const inviteEmail = ref('')
const password = ref('')
const confirmPassword = ref('')
const redeemLoading = ref(false)
const redeemError = ref<string | null>(null)
const googleLoading = ref(false)

onMounted(async () => {
    if (!token.value) {
        lookupState.value = 'invalid'
        return
    }
    try {
        const result = await lookupInvite({ token: token.value })
        if (result.valid) {
            inviteEmail.value = result.email
            lookupState.value = 'valid'
        } else {
            lookupError.value = 'This invite link is invalid or has expired.'
            lookupState.value = 'invalid'
        }
    } catch (e: unknown) {
        const httpStatus = (e as { response?: { status?: number } }).response?.status
        if (httpStatus === 429) {
            lookupError.value = 'Too many attempts — try again later'
        } else if (!navigator.onLine || (e instanceof Error && e.message === 'Network Error')) {
            lookupError.value = 'Unable to reach server'
        } else {
            lookupError.value = 'This invite link is invalid or has expired.'
        }
        lookupState.value = 'invalid'
    }
})

async function handleRedeem() {
    if (password.value !== confirmPassword.value) {
        redeemError.value = 'Passwords do not match'
        return
    }
    redeemError.value = null
    redeemLoading.value = true
    try {
        await redeemInvite({ token: token.value, password: password.value })
        // Force-refresh to pick up the newly set session cookie.
        await refreshStatus()
        router.push('/dashboard')
    } catch (e: unknown) {
        const httpStatus = (e as { response?: { status?: number } }).response?.status
        if (httpStatus === 404 || httpStatus === 410) {
            redeemError.value = 'This invite link is invalid or has expired'
        } else if (httpStatus === 429) {
            redeemError.value = 'Too many attempts — try again later'
        } else {
            redeemError.value = e instanceof Error ? e.message : 'Account creation failed'
        }
    } finally {
        redeemLoading.value = false
    }
}

async function startGoogleSignup() {
    googleLoading.value = true
    try {
        const { authorization_url } = await initiateGoogleInvite({ token: token.value })
        window.location.href = authorization_url
    } catch (e: unknown) {
        const httpStatus = (e as { response?: { status?: number } }).response?.status
        if (httpStatus === 400) {
            redeemError.value = 'This invite link is invalid or has expired.'
        } else if (httpStatus === 429) {
            redeemError.value = 'Too many attempts — try again later'
        } else if (httpStatus === 503) {
            redeemError.value = 'Google sign-in is not available right now.'
        } else {
            redeemError.value = e instanceof Error ? e.message : 'Google sign-in failed'
        }
        googleLoading.value = false
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

.loading-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 1rem 0;
}

.loading-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.invite-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    text-align: center;
    padding: 0.5rem 0;
}

.invite-greeting {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin: 0;
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

.field-input--readonly {
    opacity: 0.7;
    cursor: default;
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

.google-btn:hover:not(:disabled) {
    background: var(--color-accent);
}

.google-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.google-icon {
    width: 1.125rem;
    height: 1.125rem;
    flex-shrink: 0;
}
</style>
