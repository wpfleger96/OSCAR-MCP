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

                <GoogleSignInButton
                    label="Sign up with Google"
                    :disabled="googleLoading"
                    :loading="googleLoading"
                    @click="startGoogleSignup"
                />
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import GoogleSignInButton from '@/components/GoogleSignInButton.vue'
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

.field-input--readonly {
    opacity: 0.7;
    cursor: default;
}
</style>
