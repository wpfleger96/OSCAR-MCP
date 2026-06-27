<template>
    <div class="session-list">
        <h1 class="page-title">Sessions</h1>

        <!-- Filter Panel -->
        <div class="filter-panel">
            <input
                type="date"
                :value="fromDate ? formatIso(fromDate) : ''"
                class="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                @input="onFromDateChange"
            />
            <input
                type="date"
                :value="toDate ? formatIso(toDate) : ''"
                class="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                @input="onToDateChange"
            />
            <Select v-model="selectedDeviceStr">
                <SelectTrigger class="w-[200px]">
                    <SelectValue placeholder="All devices" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="">All devices</SelectItem>
                    <SelectItem v-for="opt in deviceOptions" :key="opt.value" :value="opt.value">
                        {{ opt.label }}
                    </SelectItem>
                </SelectContent>
            </Select>
            <Toggle v-model:pressed="includeDisabled" variant="outline" size="sm">
                <Eye v-if="includeDisabled" class="mr-2 h-4 w-4" />
                <EyeOff v-else class="mr-2 h-4 w-4" />
                {{ includeDisabled ? 'Show Disabled' : 'Active Only' }}
            </Toggle>
            <Button v-if="hasFilters" variant="outline" size="sm" @click="clearFilters">
                <FilterX class="mr-2 h-4 w-4" />
                Clear
            </Button>
        </div>

        <Table class="sessions-table">
            <TableHeader>
                <TableRow>
                    <TableHead style="min-width: 180px">Date</TableHead>
                    <TableHead style="width: 100px">Duration</TableHead>
                    <TableHead style="width: 80px">AHI</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead style="width: 90px">Status</TableHead>
                    <TableHead style="width: 180px">Actions</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                <TableRow v-if="loading">
                    <TableCell :colspan="6" class="py-8 text-center">
                        <Loader2 class="h-6 w-6 animate-spin mx-auto text-muted-foreground" />
                    </TableCell>
                </TableRow>
                <TableRow v-else-if="!sessions.length">
                    <TableCell :colspan="6" class="py-8 text-center text-muted-foreground">
                        No sessions found.
                    </TableCell>
                </TableRow>
                <template v-else>
                    <TableRow v-for="session in sessions" :key="session.id">
                        <TableCell>
                            <RouterLink
                                :to="{ name: 'session-detail', params: { id: session.id } }"
                                class="text-primary no-underline hover:underline"
                            >
                                {{ formatDateTime(session.start_time) }}
                            </RouterLink>
                        </TableCell>
                        <TableCell>{{ session.duration_hours.toFixed(1) }}h</TableCell>
                        <TableCell>
                            <span :class="ahiClass(session.ahi)">
                                {{ session.ahi?.toFixed(1) ?? '---' }}
                            </span>
                        </TableCell>
                        <TableCell>{{ session.manufacturer }} {{ session.model }}</TableCell>
                        <TableCell>
                            <Badge
                                v-if="session.enabled"
                                class="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
                            >
                                Active
                            </Badge>
                            <Badge v-else variant="secondary">Disabled</Badge>
                        </TableCell>
                        <TableCell>
                            <div class="row-actions">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    :title="session.enabled ? 'Disable' : 'Enable'"
                                    @click.stop="toggleEnabled(session)"
                                >
                                    <Ban v-if="session.enabled" class="h-4 w-4" />
                                    <Check v-else class="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    title="Events"
                                    @click.stop="
                                        router.push({
                                            name: 'session-events',
                                            params: { id: session.id },
                                        })
                                    "
                                >
                                    <BarChart3 class="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    title="Delete"
                                    class="text-destructive hover:text-destructive"
                                    @click.stop="confirmDelete(session)"
                                >
                                    <Trash2 class="h-4 w-4" />
                                </Button>
                            </div>
                        </TableCell>
                    </TableRow>
                </template>
            </TableBody>
        </Table>

        <div v-if="totalRecords > pageSize" class="flex items-center justify-between px-2 py-4">
            <span class="text-sm text-muted-foreground">
                {{ offset + 1 }}–{{ Math.min(offset + pageSize, totalRecords) }} of
                {{ totalRecords }}
            </span>
            <div class="flex gap-2">
                <Button
                    variant="outline"
                    size="sm"
                    :disabled="offset === 0"
                    @click="fetchPage(offset - pageSize)"
                >
                    Previous
                </Button>
                <Button
                    variant="outline"
                    size="sm"
                    :disabled="offset + pageSize >= totalRecords"
                    @click="fetchPage(offset + pageSize)"
                >
                    Next
                </Button>
            </div>
        </div>

        <div v-if="error" class="mt-4 text-destructive">{{ error }}</div>

        <DeleteConfirmDialog
            v-model:visible="deleteDialogVisible"
            title="Delete Session"
            :message="deleteMessage"
            :loading="deletePreviewLoading"
            :deleting="deleting"
            @confirm="executeDelete"
        >
            <template v-if="deletePreview" #preview>
                <div class="flex gap-5 text-sm text-muted-foreground">
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
import { Eye, EyeOff, Ban, Check, BarChart3, Trash2, FilterX, Loader2 } from '@lucide/vue'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Toggle } from '@/components/ui/toggle'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import { getSessions, updateSession, deleteSessions, getSessionDeletePreview } from '@/api/sessions'
import { getDevices } from '@/api/devices'
import { ahiClass } from '@/utils/format'
import { formatDateTime, formatIso } from '@/utils/formatting'
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

// Bridge between null-based selectedDevice ref and string-based Select v-model
const selectedDeviceStr = computed({
    get: () => selectedDevice.value ?? '',
    set: (v: string) => {
        selectedDevice.value = v === '' ? null : v
    },
})

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

function onFromDateChange(event: Event): void {
    const input = event.target as HTMLInputElement
    fromDate.value = input.value ? new Date(input.value + 'T00:00:00') : null
}

function onToDateChange(event: Event): void {
    const input = event.target as HTMLInputElement
    toDate.value = input.value ? new Date(input.value + 'T00:00:00') : null
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
    deleteMessage.value = `Delete session from ${formatDateTime(session.start_time)}? This cannot be undone.`
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

.row-actions {
    display: flex;
    gap: 0.25rem;
}
</style>
