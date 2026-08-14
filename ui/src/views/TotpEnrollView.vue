<template>
    <div class="totp-enroll-view">
        <div class="enroll-card">
            <h1 class="enroll-title">Two-factor authentication required</h1>
            <p class="enroll-desc">
                Your administrator requires two-factor authentication before you can access the
                application. Complete the setup below to continue.
            </p>

            <TotpEnrollmentWizard @done="onDone" />

            <div class="enroll-footer">
                <button type="button" class="logout-link" @click="handleLogout">Sign out</button>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import TotpEnrollmentWizard from '@/components/TotpEnrollmentWizard.vue'
import { useAuth } from '@/composables/useAuth'
import { resolveLandingPath } from '@/router'

const router = useRouter()
const { logout } = useAuth()

async function onDone() {
    const landing = await resolveLandingPath()
    router.push(landing)
}

function handleLogout() {
    void logout()
}
</script>

<style scoped>
.totp-enroll-view {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
    background: var(--color-background);
}

.enroll-card {
    width: 100%;
    max-width: 480px;
    background: var(--color-card);
    border: 1px solid var(--color-border);
    border-radius: 12px;
    padding: 2rem;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
}

.enroll-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--color-foreground);
    margin: 0;
}

.enroll-desc {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin: 0;
}

.enroll-footer {
    border-top: 1px solid var(--color-border);
    padding-top: 1rem;
    text-align: center;
}

.logout-link {
    background: none;
    border: none;
    color: var(--color-muted-foreground);
    font-size: 0.875rem;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 2px;
}

.logout-link:hover {
    color: var(--color-foreground);
}
</style>
