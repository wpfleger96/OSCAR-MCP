<template>
    <div class="profiles-view">
        <h1 class="page-title">Profiles</h1>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-5 w-5 animate-spin" />
            <span>Loading profiles…</span>
        </div>

        <div v-else-if="fetchError" class="error-state">
            <span>{{ fetchError }}</span>
        </div>

        <template v-else>
            <div class="section-card">
                <ul class="profile-list">
                    <li
                        v-for="profile in profiles"
                        :key="profile.id"
                        class="profile-item"
                        :class="{ 'profile-item--active': profile.id === activeProfileId }"
                    >
                        <template v-if="editingId === profile.id">
                            <input
                                v-model="editName"
                                class="profile-name-input"
                                @keydown.enter.prevent="saveRename(profile.id)"
                                @keydown.escape.prevent="cancelEdit"
                            />
                            <button class="action-btn" @click="saveRename(profile.id)">Save</button>
                            <button class="action-btn action-btn--ghost" @click="cancelEdit">
                                Cancel
                            </button>
                        </template>
                        <template v-else>
                            <div class="profile-info">
                                <span class="profile-name">{{ profile.name }}</span>
                                <span v-if="profile.created_at" class="profile-date">
                                    {{ formatDate(profile.created_at) }}
                                </span>
                            </div>
                            <div class="profile-badges">
                                <span v-if="profile.id === activeProfileId" class="active-badge">
                                    Active
                                </span>
                                <span
                                    v-if="profile.is_default ?? profile.id === activeProfileId"
                                    class="default-badge"
                                >
                                    Default
                                </span>
                            </div>
                            <div class="profile-actions">
                                <button
                                    class="action-btn action-btn--ghost"
                                    @click="startEdit(profile)"
                                >
                                    Rename
                                </button>
                                <button
                                    v-if="!(profile.is_default ?? profile.id === activeProfileId)"
                                    class="action-btn action-btn--ghost"
                                    @click="setDefault(profile.id)"
                                >
                                    Set default
                                </button>
                            </div>
                        </template>
                    </li>
                </ul>
            </div>

            <p v-if="actionError" class="action-error">{{ actionError }}</p>

            <div class="section-card">
                <h2>Create profile</h2>
                <form class="create-form" @submit.prevent="handleCreate">
                    <input
                        v-model="newProfileName"
                        type="text"
                        placeholder="Profile name"
                        class="field-input"
                        required
                        :disabled="creating"
                    />
                    <Button type="submit" :disabled="creating">
                        <Loader2 v-if="creating" class="h-4 w-4 animate-spin mr-2" />
                        Create
                    </Button>
                </form>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { listProfiles, createProfile, updateProfile, setDefaultProfile } from '@/api/profiles'
import { useAuth } from '@/composables/useAuth'
import type { ProfileResponse } from '@/types'

const { activeProfileId, refreshStatus } = useAuth()

const profiles = ref<ProfileResponse[]>([])
const loading = ref(true)
const fetchError = ref<string | null>(null)
const actionError = ref<string | null>(null)

const editingId = ref<number | null>(null)
const editName = ref('')

const newProfileName = ref('')
const creating = ref(false)

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    })
}

async function loadProfiles() {
    loading.value = true
    fetchError.value = null
    try {
        profiles.value = await listProfiles()
    } catch (e: unknown) {
        fetchError.value = e instanceof Error ? e.message : 'Failed to load profiles'
    } finally {
        loading.value = false
    }
}

onMounted(loadProfiles)

function startEdit(profile: ProfileResponse) {
    editingId.value = profile.id
    editName.value = profile.name
    actionError.value = null
}

function cancelEdit() {
    editingId.value = null
    editName.value = ''
}

async function saveRename(profileId: number) {
    const name = editName.value.trim()
    if (!name) {
        cancelEdit()
        return
    }
    actionError.value = null
    try {
        const updated = await updateProfile(profileId, { name })
        const idx = profiles.value.findIndex((p) => p.id === profileId)
        if (idx !== -1) profiles.value[idx] = updated
        cancelEdit()
        // Refresh shared auth store so AppSidebar and ImportView show the new name.
        // Fire-and-forget: a refresh failure must not report the committed rename as failed.
        refreshStatus().catch(() => {})
    } catch (e: unknown) {
        actionError.value = e instanceof Error ? e.message : 'Rename failed'
    }
}

async function setDefault(profileId: number) {
    actionError.value = null
    try {
        const updated = await setDefaultProfile(profileId)
        // Mark the new default; clear is_default on others.
        profiles.value = profiles.value.map((p) =>
            p.id === profileId ? updated : { ...p, is_default: false },
        )
        // Refresh shared auth store so profile selectors reflect the change.
        // Fire-and-forget: a refresh failure must not report the committed change as failed.
        refreshStatus().catch(() => {})
    } catch (e: unknown) {
        actionError.value = e instanceof Error ? e.message : 'Failed to set default profile'
    }
}

async function handleCreate() {
    const name = newProfileName.value.trim()
    if (!name) return
    creating.value = true
    actionError.value = null
    try {
        const created = await createProfile({ name })
        profiles.value.push(created)
        newProfileName.value = ''
        // Refresh shared auth store so AppSidebar and ImportView show the new profile.
        // Fire-and-forget: a refresh failure must not report the committed create as failed.
        refreshStatus().catch(() => {})
    } catch (e: unknown) {
        actionError.value = e instanceof Error ? e.message : 'Failed to create profile'
    } finally {
        creating.value = false
    }
}
</script>

<style scoped>
.profiles-view {
    max-width: 600px;
    margin: 0 auto;
    padding: 1.5rem;
}

.profile-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}

.profile-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.625rem 0.75rem;
    border-radius: 6px;
    transition: background 0.1s;
}

.profile-item--active {
    background: hsl(from var(--color-primary) h s l / 0.06);
}

.profile-info {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
}

.profile-name {
    font-size: 0.9rem;
    color: var(--color-foreground);
}

.profile-date {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
}

.profile-name-input {
    flex: 1;
    height: 1.875rem;
    border: 1px solid var(--color-input);
    border-radius: 4px;
    padding: 0 0.5rem;
    font-size: 0.875rem;
    background: transparent;
    color: var(--color-foreground);
    outline: none;
}

.profile-name-input:focus {
    border-color: var(--color-primary);
}

.profile-badges {
    display: flex;
    gap: 0.25rem;
}

.active-badge,
.default-badge {
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.125rem 0.5rem;
    border-radius: 999px;
}

.active-badge {
    color: var(--color-primary);
    background: hsl(from var(--color-primary) h s l / 0.1);
}

.default-badge {
    color: var(--color-muted-foreground);
    background: var(--color-accent);
}

.profile-actions {
    display: flex;
    gap: 0.25rem;
    margin-left: auto;
}

.action-btn {
    font-size: 0.8rem;
    padding: 0.25rem 0.625rem;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-card);
    color: var(--color-foreground);
    cursor: pointer;
    transition: background 0.1s;
}

.action-btn:hover {
    background: var(--color-accent);
}

.action-btn--ghost {
    background: transparent;
    border-color: transparent;
}

.action-btn--ghost:hover {
    background: var(--color-accent);
    border-color: var(--color-border);
}

.action-error {
    font-size: 0.875rem;
    color: var(--color-destructive);
    padding: 0 0.25rem;
}

.create-form {
    display: flex;
    gap: 0.75rem;
    align-items: center;
}

.field-input {
    flex: 1;
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

.field-input:focus {
    border-color: var(--color-primary);
}

.field-input:disabled {
    opacity: 0.6;
}
</style>
