<template>
    <div class="account-view">
        <h1 class="page-title">Account</h1>

        <p v-if="isDemo" role="alert" class="demo-banner">Demo accounts are read-only.</p>

        <div v-if="meLoading" class="loading-state">
            <Loader2 class="h-5 w-5 animate-spin" />
            <span>Loading…</span>
        </div>

        <div v-else-if="meError" class="error-state">
            <span>{{ meError }}</span>
        </div>

        <template v-else-if="me">
            <!-- Account card -->
            <div class="section-card">
                <h2>Account</h2>

                <div class="field-row">
                    <span class="field-label">Email</span>
                    <span class="field-value">{{ me.email }}</span>
                    <span class="role-badge">{{ me.role }}</span>
                </div>

                <form class="inline-form" @submit.prevent="saveDisplayName">
                    <label for="display-name" class="field-label">Display name</label>
                    <div class="inline-row">
                        <input
                            id="display-name"
                            v-model="displayName"
                            type="text"
                            class="field-input"
                            placeholder="Optional display name"
                            :disabled="isDemo || displayNameSaving"
                        />
                        <button
                            type="submit"
                            class="save-btn"
                            :disabled="isDemo || displayNameSaving"
                        >
                            <Loader2 v-if="displayNameSaving" class="h-4 w-4 animate-spin" />
                            <span v-else>Save</span>
                        </button>
                    </div>
                    <p v-if="displayNameError" role="alert" class="form-error">
                        {{ displayNameError }}
                    </p>
                    <p v-if="displayNameSuccess" class="form-success">{{ displayNameSuccess }}</p>
                </form>
            </div>

            <!-- Password card -->
            <div class="section-card">
                <h2>{{ me.has_password ? 'Password' : 'Set password' }}</h2>

                <form class="stacked-form" @submit.prevent="savePassword">
                    <div v-if="me.has_password" class="field-group">
                        <label for="current-password" class="field-label">Current password</label>
                        <input
                            id="current-password"
                            v-model="currentPassword"
                            type="password"
                            autocomplete="current-password"
                            class="field-input"
                            :disabled="isDemo || passwordSaving"
                        />
                    </div>

                    <div class="field-group">
                        <label for="new-password" class="field-label">New password</label>
                        <input
                            id="new-password"
                            v-model="newPassword"
                            type="password"
                            autocomplete="new-password"
                            class="field-input"
                            :disabled="isDemo || passwordSaving"
                        />
                    </div>

                    <div class="field-group">
                        <label for="confirm-password" class="field-label">Confirm password</label>
                        <input
                            id="confirm-password"
                            v-model="confirmPassword"
                            type="password"
                            autocomplete="new-password"
                            class="field-input"
                            :disabled="isDemo || passwordSaving"
                        />
                    </div>

                    <p v-if="passwordError" role="alert" class="form-error">{{ passwordError }}</p>
                    <p v-if="passwordSuccess" class="form-success">{{ passwordSuccess }}</p>

                    <button type="submit" class="save-btn" :disabled="isDemo || passwordSaving">
                        <Loader2 v-if="passwordSaving" class="h-4 w-4 animate-spin" />
                        <span v-else>{{
                            me.has_password ? 'Update password' : 'Set password'
                        }}</span>
                    </button>
                </form>
            </div>

            <!-- Preferences card -->
            <div class="section-card">
                <h2>Preferences</h2>

                <div v-if="prefsLoading" class="loading-state">
                    <Loader2 class="h-4 w-4 animate-spin" />
                    <span>Loading preferences…</span>
                </div>

                <div v-else-if="prefsError" class="error-state">
                    <span>{{ prefsError }}</span>
                </div>

                <form v-else class="stacked-form" @submit.prevent="savePreferences">
                    <div class="field-group">
                        <label for="landing-page" class="field-label">Landing page</label>
                        <select
                            id="landing-page"
                            v-model="prefLandingPage"
                            class="field-select"
                            :disabled="isDemo || prefSaving"
                        >
                            <option value="dashboard">Dashboard</option>
                            <option value="sessions">Sessions</option>
                            <option value="days">Days</option>
                        </select>
                    </div>

                    <div class="field-group">
                        <label for="date-format" class="field-label">Date format</label>
                        <select
                            id="date-format"
                            v-model="prefDateFormat"
                            class="field-select"
                            :disabled="isDemo || prefSaving"
                        >
                            <option value="iso">ISO (2026-08-05)</option>
                            <option value="locale">Browser locale</option>
                            <option value="short">Short (8/5/26)</option>
                        </select>
                    </div>

                    <p v-if="prefSaveError" role="alert" class="form-error">{{ prefSaveError }}</p>
                    <p v-if="prefSuccess" class="form-success">{{ prefSuccess }}</p>

                    <button type="submit" class="save-btn" :disabled="isDemo || prefSaving">
                        <Loader2 v-if="prefSaving" class="h-4 w-4 animate-spin" />
                        <span v-else>Save preferences</span>
                    </button>
                </form>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Loader2 } from '@lucide/vue'
import { useApiLoad } from '@/composables/useApiLoad'
import { useAuth } from '@/composables/useAuth'
import {
    getMe,
    updateDisplayName,
    changePassword,
    getPreferences,
    updatePreferences,
} from '@/api/me'
import type { components } from '@/types/generated'

type UserPreferences = components['schemas']['UserPreferences']

const { role, refreshStatus } = useAuth()
const isDemo = computed(() => role.value === 'demo')

// ── Account ──────────────────────────────────────────────────────────────────

const { data: me, loading: meLoading, error: meError, reload: reloadMe } = useApiLoad(getMe)

const displayName = ref('')
const displayNameSaving = ref(false)
const displayNameError = ref<string | null>(null)
const displayNameSuccess = ref<string | null>(null)

watch(me, (val) => {
    if (val) displayName.value = val.display_name ?? ''
})

async function saveDisplayName() {
    if (isDemo.value) return
    displayNameError.value = null
    displayNameSuccess.value = null
    displayNameSaving.value = true
    try {
        await updateDisplayName({ display_name: displayName.value.trim() || null })
        await refreshStatus()
        displayNameSuccess.value = 'Display name updated'
    } catch (e: unknown) {
        displayNameError.value = e instanceof Error ? e.message : 'Failed to update display name'
    } finally {
        displayNameSaving.value = false
    }
}

// ── Password ──────────────────────────────────────────────────────────────────

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordSaving = ref(false)
const passwordError = ref<string | null>(null)
const passwordSuccess = ref<string | null>(null)

async function savePassword() {
    if (isDemo.value) return
    passwordError.value = null
    passwordSuccess.value = null
    if (newPassword.value !== confirmPassword.value) {
        passwordError.value = 'Passwords do not match'
        return
    }
    passwordSaving.value = true
    try {
        await changePassword({
            new_password: newPassword.value,
            ...(me.value?.has_password ? { current_password: currentPassword.value } : {}),
        })
        passwordSuccess.value = 'Password updated'
        currentPassword.value = ''
        newPassword.value = ''
        confirmPassword.value = ''
        await reloadMe()
    } catch (e: unknown) {
        passwordError.value = e instanceof Error ? e.message : 'Failed to change password'
    } finally {
        passwordSaving.value = false
    }
}

// ── Preferences ───────────────────────────────────────────────────────────────

const { data: prefs, loading: prefsLoading, error: prefsError } = useApiLoad(getPreferences)

const prefLandingPage = ref<UserPreferences['landing_page']>('dashboard')
const prefDateFormat = ref<UserPreferences['date_format']>('iso')
const prefBaseline = ref<UserPreferences | null>(null)
const prefSaving = ref(false)
const prefSaveError = ref<string | null>(null)
const prefSuccess = ref<string | null>(null)

watch(prefs, (val) => {
    if (val) {
        prefLandingPage.value = val.landing_page
        prefDateFormat.value = val.date_format
        prefBaseline.value = { landing_page: val.landing_page, date_format: val.date_format }
    }
})

async function savePreferences() {
    if (isDemo.value) return
    prefSaveError.value = null
    prefSuccess.value = null

    const update: components['schemas']['UserPreferencesUpdate'] = {}
    if (prefBaseline.value?.landing_page !== prefLandingPage.value) {
        update.landing_page = prefLandingPage.value
    }
    if (prefBaseline.value?.date_format !== prefDateFormat.value) {
        update.date_format = prefDateFormat.value
    }
    if (Object.keys(update).length === 0) {
        prefSuccess.value = 'No changes to save'
        return
    }

    prefSaving.value = true
    try {
        const result = await updatePreferences(update)
        prefBaseline.value = { landing_page: result.landing_page, date_format: result.date_format }
        prefLandingPage.value = result.landing_page
        prefDateFormat.value = result.date_format
        prefSuccess.value = 'Preferences saved'
    } catch (e: unknown) {
        prefSaveError.value = e instanceof Error ? e.message : 'Failed to save preferences'
    } finally {
        prefSaving.value = false
    }
}
</script>

<style scoped>
.account-view {
    max-width: 560px;
    margin: 0 auto;
    padding: 1.5rem;
}

.demo-banner {
    background: hsl(from var(--color-warning) h s l / 0.12);
    border: 1px solid hsl(from var(--color-warning) h s l / 0.4);
    border-radius: 6px;
    color: var(--color-warning);
    font-size: 0.875rem;
    font-weight: 500;
    padding: 0.625rem 0.875rem;
    margin-bottom: 1.25rem;
}

.field-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
}

.field-label {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--color-foreground);
    min-width: 7rem;
}

.field-value {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.role-badge {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    background: hsl(from var(--color-primary) h s l / 0.1);
    color: var(--color-primary);
}

.inline-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.inline-row {
    display: flex;
    gap: 0.5rem;
}

.stacked-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.field-group {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}

.field-input,
.field-select {
    height: 2.25rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0 0.75rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    outline: none;
    transition: border-color 0.15s;
}

.field-input {
    flex: 1;
}

.field-select {
    cursor: pointer;
}

.field-input:focus,
.field-select:focus {
    border-color: var(--color-primary);
}

.field-input:disabled,
.field-select:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.save-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.375rem;
    height: 2.25rem;
    padding: 0 1rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-border);
    background: var(--color-primary);
    color: var(--color-primary-foreground);
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: opacity 0.15s;
    align-self: flex-start;
}

.save-btn:hover:not(:disabled) {
    opacity: 0.9;
}

.save-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.form-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin: 0;
}

.form-success {
    font-size: 0.875rem;
    color: var(--color-success);
    margin: 0;
}
</style>
