<template>
    <div class="session-list">
        <h1 class="page-title">Sessions</h1>

        <!-- Filter Panel -->
        <div class="filter-panel">
            <DatePicker
                v-model="fromDate"
                placeholder="From date"
                date-format="yy-mm-dd"
                show-icon
            />
            <DatePicker v-model="toDate" placeholder="To date" date-format="yy-mm-dd" show-icon />
            <Select
                v-model="selectedDevice"
                :options="deviceOptions"
                option-label="label"
                option-value="value"
                placeholder="All devices"
                show-clear
            />
            <ToggleButton
                v-model="includeDisabled"
                on-label="Show Disabled"
                off-label="Active Only"
                on-icon="pi pi-eye"
                off-icon="pi pi-eye-slash"
            />
            <Button
                v-if="hasFilters"
                label="Clear"
                icon="pi pi-filter-slash"
                severity="secondary"
                size="small"
                @click="clearFilters"
            />
        </div>

        <DataTable
            :value="sessions"
            :loading="loading"
            :lazy="true"
            :rows="pageSize"
            :total-records="totalRecords"
            :first="offset"
            paginator
            striped-rows
            class="sessions-table"
            @page="onPage"
        >
            <template #empty>
                <div class="table-empty">No sessions found.</div>
            </template>
            <Column field="start_time" header="Date" style="min-width: 180px">
                <template #body="{ data }: { data: SessionListItem }">
                    <RouterLink
                        :to="{ name: 'session-detail', params: { id: data.id } }"
                        class="session-link"
                    >
                        {{ formatDate(data.start_time) }}
                    </RouterLink>
                </template>
            </Column>
            <Column field="duration_hours" header="Duration" style="width: 100px">
                <template #body="{ data }: { data: SessionListItem }">
                    {{ data.duration_hours.toFixed(1) }}h
                </template>
            </Column>
            <Column field="ahi" header="AHI" style="width: 80px">
                <template #body="{ data }: { data: SessionListItem }">
                    <span :class="ahiClass(data.ahi)">{{ data.ahi?.toFixed(1) ?? '---' }}</span>
                </template>
            </Column>
            <Column header="Device">
                <template #body="{ data }: { data: SessionListItem }">
                    {{ data.manufacturer }} {{ data.model }}
                </template>
            </Column>
            <Column field="enabled" header="Status" style="width: 90px">
                <template #body="{ data }: { data: SessionListItem }">
                    <Tag
                        :value="data.enabled ? 'Active' : 'Disabled'"
                        :severity="data.enabled ? 'success' : 'secondary'"
                    />
                </template>
            </Column>
            <Column header="Actions" style="width: 180px">
                <template #body="{ data }: { data: SessionListItem }">
                    <div class="row-actions">
                        <Button
                            :icon="data.enabled ? 'pi pi-ban' : 'pi pi-check'"
                            :title="data.enabled ? 'Disable' : 'Enable'"
                            size="small"
                            severity="secondary"
                            text
                            rounded
                            @click.stop="toggleEnabled(data)"
                        />
                        <Button
                            icon="pi pi-chart-bar"
                            title="Events"
                            size="small"
                            severity="secondary"
                            text
                            rounded
                            @click.stop="
                                router.push({ name: 'session-events', params: { id: data.id } })
                            "
                        />
                        <Button
                            icon="pi pi-trash"
                            title="Delete"
                            size="small"
                            severity="danger"
                            text
                            rounded
                            @click.stop="confirmDelete(data)"
                        />
                    </div>
                </template>
            </Column>
        </DataTable>
        <div v-if="error" class="error-msg">{{ error }}</div>

        <DeleteConfirmDialog
            v-model:visible="deleteDialogVisible"
            title="Delete Session"
            :message="deleteMessage"
            :loading="deletePreviewLoading"
            :deleting="deleting"
            @confirm="executeDelete"
        >
            <template v-if="deletePreview" #preview>
                <div class="delete-stats">
                    <span>{{ deletePreview.event_count }} events</span>
                    <span>{{ deletePreview.waveform_count }} waveforms</span>
                    <span>{{ deletePreview.stats_count }} stats records</span>
                </div>
            </template>
        </DeleteConfirmDialog>
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import Button from 'primevue/button'
import DatePicker from 'primevue/datepicker'
import Select from 'primevue/select'
import ToggleButton from 'primevue/togglebutton'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import { getSessions, updateSession, deleteSessions, getSessionDeletePreview } from '@/api/sessions'
import { getDevices } from '@/api/devices'
import { ahiClass } from '@/utils/format'
import type { SessionListItem, DeletePreview, DeviceInfo } from '@/types'

const router = useRouter()
const route = useRoute()
const sessions = ref<SessionListItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const totalRecords = ref(0)
const offset = ref(0)
const pageSize = 25

// Filters
const fromDate = ref<Date | null>(route.query.from ? new Date(route.query.from as string) : null)
const toDate = ref<Date | null>(route.query.to ? new Date(route.query.to as string) : null)
const selectedDevice = ref<string | null>(null)
const includeDisabled = ref(false)
const devices = ref<DeviceInfo[]>([])

const deviceOptions = computed(() =>
    devices.value.map((d) => ({
        label: `${d.manufacturer} ${d.model}`,
        value: `${d.manufacturer} ${d.model}`,
    })),
)

const hasFilters = computed(
    () => fromDate.value != null || toDate.value != null || selectedDevice.value != null,
)

// Delete
const deleteDialogVisible = ref(false)
const deletePreviewLoading = ref(false)
const deleting = ref(false)
const deletePreview = ref<DeletePreview | null>(null)
const deleteTargetId = ref<number | null>(null)
const deleteMessage = ref('')

function formatDate(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

function formatIso(d: Date): string {
    return d.toISOString().slice(0, 10)
}

async function fetchPage(newOffset: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
        const result = await getSessions({
            limit: pageSize,
            offset: newOffset,
            sort_by: 'date-desc',
            include_disabled: includeDisabled.value || undefined,
            from_date: fromDate.value ? formatIso(fromDate.value) : undefined,
            to_date: toDate.value ? formatIso(toDate.value) : undefined,
            device: selectedDevice.value ?? undefined,
        })
        sessions.value = result.items
        totalRecords.value = result.total
        offset.value = newOffset
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to load sessions'
    } finally {
        loading.value = false
    }
}

function onPage(event: { first: number }): void {
    void fetchPage(event.first)
}

function clearFilters(): void {
    fromDate.value = null
    toDate.value = null
    selectedDevice.value = null
}

async function toggleEnabled(session: SessionListItem): Promise<void> {
    try {
        await updateSession(session.id, { enabled: !session.enabled })
        void fetchPage(offset.value)
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to update session'
    }
}

async function confirmDelete(session: SessionListItem): Promise<void> {
    deleteTargetId.value = session.id
    deleteMessage.value = `Delete session from ${formatDate(session.start_time)}? This cannot be undone.`
    deleteDialogVisible.value = true
    deletePreviewLoading.value = true
    deletePreview.value = null
    try {
        deletePreview.value = await getSessionDeletePreview(session.id)
    } finally {
        deletePreviewLoading.value = false
    }
}

async function executeDelete(): Promise<void> {
    if (deleteTargetId.value == null) return
    deleting.value = true
    try {
        await deleteSessions({ session_ids: [deleteTargetId.value] })
        deleteDialogVisible.value = false
        void fetchPage(offset.value)
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to delete session'
    } finally {
        deleting.value = false
    }
}

// Re-fetch when filters change
watch([fromDate, toDate, selectedDevice, includeDisabled], () => void fetchPage(0))

onMounted(async () => {
    const [, deviceList] = await Promise.all([fetchPage(0), getDevices()])
    devices.value = deviceList
})
</script>

<style scoped>
.session-list {
    max-width: 1200px;
}

.filter-panel {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
}

.sessions-table {
    cursor: default;
}

.session-link {
    color: var(--p-primary-color, #3b82f6);
    text-decoration: none;
}
.session-link:hover {
    text-decoration: underline;
}

.row-actions {
    display: flex;
    gap: 0.25rem;
}

.table-empty {
    padding: 2rem;
    text-align: center;
    color: var(--p-text-muted-color, #6b7280);
}

.error-msg {
    margin-top: 1rem;
    color: var(--p-red-500, #ef4444);
}

.delete-stats {
    display: flex;
    gap: 1.25rem;
    font-size: 0.9rem;
    color: var(--p-text-muted-color, #6b7280);
}
</style>
