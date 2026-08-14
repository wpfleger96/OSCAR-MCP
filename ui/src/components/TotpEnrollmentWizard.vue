<template>
    <div class="totp-wizard">
        <!-- Step 1: QR code + secret -->
        <div v-if="step === 1" class="wizard-step">
            <div v-if="setupLoading" class="wizard-loading">
                <Loader2 class="h-5 w-5 animate-spin" />
                <span>Generating setup…</span>
            </div>
            <div v-else-if="setupError" class="wizard-error" role="alert">
                {{ setupError }}
            </div>
            <template v-else-if="setup">
                <p class="wizard-hint">
                    Scan this QR code with your authenticator app (Google Authenticator, Authy,
                    1Password, etc.).
                </p>
                <div class="qr-container">
                    <img
                        :src="setup.qr_data_uri"
                        alt="TOTP QR code"
                        class="qr-image"
                        width="200"
                        height="200"
                    />
                </div>
                <p class="wizard-hint">Can't scan? Enter this code manually:</p>
                <div class="secret-row">
                    <code class="secret-code">{{ setup.secret }}</code>
                    <button
                        type="button"
                        class="copy-btn"
                        :class="{ 'copy-btn--copied': secretCopied }"
                        @click="copySecret"
                    >
                        {{ secretCopied ? 'Copied' : 'Copy' }}
                    </button>
                </div>
                <p class="wizard-hint muted">
                    Manual entry URI:
                    <code class="uri-code">{{ setup.otpauth_uri }}</code>
                </p>
                <Button class="mt-4" @click="step = 2">Continue</Button>
            </template>
        </div>

        <!-- Step 2: Verify code -->
        <div v-else-if="step === 2" class="wizard-step">
            <p class="wizard-hint">
                Enter the 6-digit code shown in your authenticator app to confirm setup.
            </p>
            <form class="code-form" @submit.prevent="submitCode">
                <input
                    id="totp-code"
                    v-model="verifyCode"
                    type="text"
                    inputmode="numeric"
                    pattern="[0-9]{6}"
                    maxlength="6"
                    placeholder="123456"
                    class="code-input"
                    autocomplete="one-time-code"
                    :disabled="verifying"
                />
                <p v-if="verifyError" class="wizard-error" role="alert">{{ verifyError }}</p>
                <div class="wizard-actions">
                    <button type="button" class="back-btn" :disabled="verifying" @click="step = 1">
                        Back
                    </button>
                    <Button type="submit" :disabled="verifying || verifyCode.length !== 6">
                        <Loader2 v-if="verifying" class="h-4 w-4 animate-spin mr-2" />
                        Confirm
                    </Button>
                </div>
            </form>
        </div>

        <!-- Step 3: Recovery codes -->
        <div v-else-if="step === 3" class="wizard-step">
            <p class="wizard-hint">
                <strong>Save these recovery codes.</strong> Each code can be used once to sign in if
                you lose access to your authenticator app. They will not be shown again.
            </p>
            <ul class="recovery-list">
                <li v-for="code in recoveryCodes" :key="code" class="recovery-code">
                    {{ code }}
                </li>
            </ul>
            <div class="recovery-actions">
                <button type="button" class="copy-btn" @click="copyAllCodes">
                    {{ codesCopied ? 'Copied!' : 'Copy all' }}
                </button>
                <button type="button" class="copy-btn" @click="downloadCodes">Download .txt</button>
            </div>
            <label class="ack-label">
                <input v-model="acknowledged" type="checkbox" class="ack-checkbox" />
                I've saved these recovery codes
            </label>
            <Button class="mt-4" :disabled="!acknowledged" @click="emit('done')">Finish</Button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { setupTotp, confirmTotp } from '@/api/totp'
import type { components } from '@/types/generated'

type TotpSetupResponse = components['schemas']['TotpSetupResponse']

const emit = defineEmits<{ done: [] }>()

const step = ref<1 | 2 | 3>(1)

// Step 1 state
const setup = ref<TotpSetupResponse | null>(null)
const setupLoading = ref(true)
const setupError = ref<string | null>(null)
const secretCopied = ref(false)

// Step 2 state
const verifyCode = ref('')
const verifying = ref(false)
const verifyError = ref<string | null>(null)

// Step 3 state
const recoveryCodes = ref<string[]>([])
const codesCopied = ref(false)
const acknowledged = ref(false)

onMounted(async () => {
    try {
        setup.value = await setupTotp()
    } catch (e: unknown) {
        setupError.value = e instanceof Error ? e.message : 'Failed to start setup'
    } finally {
        setupLoading.value = false
    }
})

async function copySecret() {
    if (!setup.value) return
    try {
        await navigator.clipboard.writeText(setup.value.secret)
        secretCopied.value = true
        setTimeout(() => {
            secretCopied.value = false
        }, 2000)
    } catch {
        // clipboard API unavailable — do nothing
    }
}

async function submitCode() {
    verifyError.value = null
    verifying.value = true
    try {
        const result = await confirmTotp({ code: verifyCode.value })
        recoveryCodes.value = result.recovery_codes
        step.value = 3
    } catch (e: unknown) {
        verifyError.value = e instanceof Error ? e.message : 'Invalid code — try again'
    } finally {
        verifying.value = false
    }
}

async function copyAllCodes() {
    try {
        await navigator.clipboard.writeText(recoveryCodes.value.join('\n'))
        codesCopied.value = true
        setTimeout(() => {
            codesCopied.value = false
        }, 2000)
    } catch {
        // clipboard API unavailable — do nothing
    }
}

function downloadCodes() {
    const text = recoveryCodes.value.join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'snore-recovery-codes.txt'
    a.click()
    URL.revokeObjectURL(url)
}
</script>

<style scoped>
.totp-wizard {
    display: flex;
    flex-direction: column;
}

.wizard-step {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.wizard-loading {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--color-muted-foreground);
    font-size: 0.875rem;
}

.wizard-hint {
    font-size: 0.875rem;
    color: var(--color-foreground);
    margin: 0;
}

.wizard-hint.muted {
    color: var(--color-muted-foreground);
    font-size: 0.8rem;
}

.wizard-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin: 0;
}

.qr-container {
    background: #fff;
    padding: 0.75rem;
    border-radius: 8px;
    display: inline-flex;
    align-self: flex-start;
}

.qr-image {
    display: block;
    width: 200px;
    height: 200px;
}

.secret-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.secret-code {
    font-family: monospace;
    font-size: 0.9rem;
    letter-spacing: 0.08em;
    background: var(--color-accent);
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    word-break: break-all;
}

.uri-code {
    font-family: monospace;
    font-size: 0.75rem;
    word-break: break-all;
}

.copy-btn {
    background: none;
    border: 1px solid var(--color-border);
    border-radius: 4px;
    padding: 0.125rem 0.5rem;
    font-size: 0.8rem;
    cursor: pointer;
    color: var(--color-foreground);
    white-space: nowrap;
}

.copy-btn--copied {
    color: var(--color-success);
    border-color: var(--color-success);
}

.copy-btn:hover {
    background: var(--color-accent);
}

.code-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.code-input {
    font-family: monospace;
    font-size: 1.25rem;
    letter-spacing: 0.2em;
    text-align: center;
    padding: 0.5rem;
    border: 1px solid var(--color-input);
    border-radius: 6px;
    background: transparent;
    color: var(--color-foreground);
    width: 9rem;
    outline: none;
}

.code-input:focus {
    border-color: var(--color-primary);
}

.wizard-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.back-btn {
    background: none;
    border: 1px solid var(--color-border);
    border-radius: 4px;
    padding: 0.375rem 0.75rem;
    font-size: 0.875rem;
    cursor: pointer;
    color: var(--color-muted-foreground);
}

.back-btn:hover {
    background: var(--color-accent);
}

.recovery-list {
    list-style: none;
    padding: 0.75rem;
    margin: 0;
    background: var(--color-accent);
    border-radius: 6px;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.25rem 1.5rem;
}

.recovery-code {
    font-family: monospace;
    font-size: 0.9rem;
    letter-spacing: 0.05em;
    color: var(--color-foreground);
}

.recovery-actions {
    display: flex;
    gap: 0.5rem;
}

.ack-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.875rem;
    cursor: pointer;
}

.ack-checkbox {
    cursor: pointer;
}
</style>
