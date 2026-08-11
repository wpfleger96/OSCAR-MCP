<template>
    <div class="rx-history">
        <h1 class="page-title">RX History</h1>

        <div v-if="loading" class="loading-state">
            <Loader2 class="h-4 w-4 animate-spin" /> Loading RX data...
        </div>

        <ErrorState v-else-if="error" :message="error" :retry="reload" />

        <template v-else-if="history.length">
            <!-- Current Settings -->
            <div v-if="current" class="section-card">
                <h2>Current Settings</h2>
                <div class="current-meta">
                    <span
                        >{{ formatDateFull(current.start_date) }} –
                        {{ formatDateFull(current.end_date) }}</span
                    >
                    <span>{{ current.days_count }} days</span>
                    <span v-if="current.device_name">{{ current.device_name }}</span>
                    <span v-if="current.avg_ahi != null"
                        >Avg AHI: {{ current.avg_ahi.toFixed(1) }}</span
                    >
                    <span v-if="current.avg_hours != null"
                        >Avg {{ current.avg_hours.toFixed(1) }} hrs/night</span
                    >
                </div>
                <div class="settings-pills">
                    <Badge
                        v-for="(value, key) in current.settings"
                        :key="key"
                        variant="secondary"
                        class="setting-pill"
                    >
                        {{ settingLabel(key) }}: {{ formatSettingValue(key, value) }}
                    </Badge>
                </div>
            </div>

            <!-- Comparison Table -->
            <div v-if="history.length > 1" class="section-card">
                <h2>Period Comparison</h2>
                <div class="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead class="whitespace-nowrap">Period</TableHead>
                                <TableHead class="whitespace-nowrap">Days</TableHead>
                                <TableHead class="whitespace-nowrap">Device</TableHead>
                                <TableHead>Settings</TableHead>
                                <TableHead class="whitespace-nowrap">Avg AHI</TableHead>
                                <TableHead class="whitespace-nowrap">Median AHI</TableHead>
                                <TableHead class="whitespace-nowrap">Avg Hours</TableHead>
                                <TableHead class="whitespace-nowrap">Avg Leak</TableHead>
                                <TableHead></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow
                                v-for="row in comparisonRows"
                                :key="row.start_date"
                                :class="{
                                    'bg-green-500/10': row.isBest,
                                    'bg-destructive/10': row.isWorst,
                                    'even:bg-muted/50': !row.isBest && !row.isWorst,
                                }"
                            >
                                <TableCell class="whitespace-nowrap">
                                    {{ formatDateFull(row.start_date) }} –
                                    {{ formatDateFull(row.end_date) }}
                                </TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.days_count
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.device_name ?? '—'
                                }}</TableCell>
                                <TableCell>{{ summarizeSettings(row.settings) }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_ahi?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.median_ahi?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_hours?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    row.avg_leak?.toFixed(1) ?? '---'
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">
                                    <Badge
                                        v-if="row.isBest"
                                        class="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
                                        >Best</Badge
                                    >
                                    <Badge v-if="row.isWorst" variant="destructive">Worst</Badge>
                                </TableCell>
                            </TableRow>
                        </TableBody>
                    </Table>
                </div>
            </div>

            <!-- Settings Changes — device changes merged with mask log, most recent first -->
            <div v-if="timelineRows.length" class="section-card">
                <h2>Settings Changes</h2>
                <div class="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead class="whitespace-nowrap">Date</TableHead>
                                <TableHead class="whitespace-nowrap">Source</TableHead>
                                <TableHead class="whitespace-nowrap">Device</TableHead>
                                <TableHead class="whitespace-nowrap">Setting</TableHead>
                                <TableHead class="whitespace-nowrap">Change</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow v-for="row in timelineRows" :key="row.key">
                                <TableCell class="whitespace-nowrap">{{
                                    formatDateFull(row.date)
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">
                                    <Badge v-if="row.source === 'device'" variant="secondary"
                                        >Device</Badge
                                    >
                                    <Badge v-else variant="outline">Mask Log</Badge>
                                </TableCell>
                                <template v-if="row.source === 'device'">
                                    <TableCell class="whitespace-nowrap">{{
                                        row.change.device_name
                                    }}</TableCell>
                                    <TableCell class="whitespace-nowrap">{{
                                        settingLabel(row.change.key)
                                    }}</TableCell>
                                    <TableCell class="whitespace-nowrap">
                                        <span class="text-muted-foreground">{{
                                            row.change.old_value != null
                                                ? formatSettingValue(
                                                      row.change.key,
                                                      row.change.old_value,
                                                  )
                                                : '—'
                                        }}</span>
                                        <span class="mx-1">→</span>
                                        <span>{{
                                            row.change.new_value != null
                                                ? formatSettingValue(
                                                      row.change.key,
                                                      row.change.new_value,
                                                  )
                                                : '—'
                                        }}</span>
                                    </TableCell>
                                </template>
                                <template v-else>
                                    <TableCell class="whitespace-nowrap">—</TableCell>
                                    <TableCell class="whitespace-nowrap">Mask</TableCell>
                                    <TableCell class="whitespace-nowrap">{{
                                        maskSummary(row.entry)
                                    }}</TableCell>
                                </template>
                            </TableRow>
                        </TableBody>
                    </Table>
                </div>
            </div>
        </template>

        <div v-else class="no-data"><Info class="h-4 w-4" /> No prescription data available.</div>

        <!-- Mask Equipment -->
        <div v-if="!loading" class="section-card">
            <h2>Mask Equipment</h2>

            <div v-if="maskLoading" class="loading-state">
                <Loader2 class="h-4 w-4 animate-spin" /> Loading mask log...
            </div>

            <ErrorState v-else-if="maskError" :message="maskError" :retry="reloadMasks" />

            <template v-else>
                <div v-if="masks.length" class="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead class="whitespace-nowrap">Start Date</TableHead>
                                <TableHead class="whitespace-nowrap">Brand</TableHead>
                                <TableHead class="whitespace-nowrap">Model</TableHead>
                                <TableHead class="whitespace-nowrap">Style</TableHead>
                                <TableHead class="whitespace-nowrap">Size</TableHead>
                                <TableHead>Notes</TableHead>
                                <TableHead v-if="canWrite"></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            <TableRow v-for="entry in masks" :key="entry.id">
                                <TableCell class="whitespace-nowrap">{{
                                    formatDateFull(entry.start_date)
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{ entry.brand }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{ entry.model }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    styleLabel(entry.style)
                                }}</TableCell>
                                <TableCell class="whitespace-nowrap">{{
                                    entry.size ?? '—'
                                }}</TableCell>
                                <TableCell>{{ entry.notes ?? '—' }}</TableCell>
                                <TableCell v-if="canWrite" class="whitespace-nowrap text-right">
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        :disabled="saving"
                                        @click="startEdit(entry)"
                                    >
                                        Edit
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        :disabled="saving"
                                        @click="startDelete(entry)"
                                    >
                                        Delete
                                    </Button>
                                </TableCell>
                            </TableRow>
                        </TableBody>
                    </Table>
                </div>
                <p v-else class="mask-empty">No mask equipment logged yet.</p>

                <p v-if="maskActionError" class="mask-action-error">{{ maskActionError }}</p>

                <form v-if="canWrite" class="mask-form" @submit.prevent="handleSubmit">
                    <h3>{{ editingId != null ? 'Edit Mask' : 'Add Mask' }}</h3>
                    <div class="mask-form-grid">
                        <div class="mask-field">
                            <label class="mask-label" for="mask-brand">Brand</label>
                            <input
                                id="mask-brand"
                                v-model="form.brand"
                                type="text"
                                class="field-input"
                                placeholder="e.g. ResMed"
                                required
                                :disabled="saving"
                            />
                        </div>
                        <div class="mask-field">
                            <label class="mask-label" for="mask-model">Model</label>
                            <input
                                id="mask-model"
                                v-model="form.model"
                                type="text"
                                class="field-input"
                                placeholder="e.g. AirFit P10"
                                required
                                :disabled="saving"
                            />
                        </div>
                        <div class="mask-field">
                            <label class="mask-label">Style</label>
                            <Select v-model="form.style">
                                <SelectTrigger class="w-full">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem
                                        v-for="opt in STYLE_OPTIONS"
                                        :key="opt.value"
                                        :value="opt.value"
                                    >
                                        {{ opt.label }}
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div class="mask-field">
                            <label class="mask-label">Start Date</label>
                            <DatePickerInput v-model="form.startDate" />
                        </div>
                        <div class="mask-field">
                            <label class="mask-label" for="mask-size">Size</label>
                            <input
                                id="mask-size"
                                v-model="form.size"
                                type="text"
                                class="field-input"
                                placeholder="Optional"
                                :disabled="saving"
                            />
                        </div>
                        <div class="mask-field">
                            <label class="mask-label" for="mask-notes">Notes</label>
                            <input
                                id="mask-notes"
                                v-model="form.notes"
                                type="text"
                                class="field-input"
                                placeholder="Optional"
                                :disabled="saving"
                            />
                        </div>
                    </div>
                    <div class="mask-form-actions">
                        <Button type="submit" :disabled="saving || !formValid">
                            <Loader2 v-if="saving" class="mr-2 h-4 w-4 animate-spin" />
                            {{ editingId != null ? 'Save' : 'Add Mask' }}
                        </Button>
                        <Button
                            v-if="editingId != null"
                            type="button"
                            variant="ghost"
                            :disabled="saving"
                            @click="resetForm"
                        >
                            Cancel
                        </Button>
                    </div>
                </form>
            </template>
        </div>

        <DeleteConfirmDialog
            v-model:visible="deleteDialogVisible"
            title="Delete mask entry"
            :message="deleteMessage"
            :loading="false"
            :deleting="deleting"
            @confirm="handleDelete"
        />
    </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Loader2, Info } from '@lucide/vue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { getRxAll } from '@/api/rx'
import {
    getMaskLog,
    createMaskLogEntry,
    updateMaskLogEntry,
    deleteMaskLogEntry,
} from '@/api/equipment'
import { useApiLoad } from '@/composables/useApiLoad'
import { useAuth } from '@/composables/useAuth'
import { formatDateFull } from '@/utils/formatting'
import { settingLabel, formatSettingValue } from '@/utils/deviceSettings'
import type { MaskLogEntryResponse, RxPeriodResponse, RxSettingChange } from '@/types'
import DatePickerInput from '@/components/DatePickerInput.vue'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'

const { canWrite } = useAuth()

const { data, loading, error, reload } = useApiLoad(() => getRxAll(), 'Failed to load RX data')

const history = computed(() => data.value?.history ?? [])
const current = computed(() => data.value?.current ?? null)

interface ComparisonRow extends RxPeriodResponse {
    isBest: boolean
    isWorst: boolean
}

const comparisonRows = computed<ComparisonRow[]>(() =>
    history.value.map((p, i) => ({
        ...p,
        isBest: data.value?.best_index === i,
        isWorst: data.value?.worst_index === i,
    })),
)

function summarizeSettings(settings: Record<string, string>): string {
    const priorityKeys = [
        'mode',
        'pressure_fixed',
        'pressure_min',
        'pressure_max',
        'ipap',
        'epap',
        'ps',
        'epr_level',
    ]
    const parts: string[] = []
    for (const k of priorityKeys) {
        if (k in settings) {
            parts.push(`${settingLabel(k)}: ${formatSettingValue(k, settings[k])}`)
            if (parts.length === 4) break
        }
    }
    if (!parts.length) {
        return Object.entries(settings)
            .slice(0, 3)
            .map(([k, v]) => `${settingLabel(k)}: ${formatSettingValue(k, v)}`)
            .join(', ')
    }
    return parts.join(', ')
}

// --- Mask equipment log ---

type MaskStyle = 'pillows' | 'nasal' | 'full_face'

const STYLE_OPTIONS: { value: MaskStyle; label: string }[] = [
    { value: 'pillows', label: 'Pillows' },
    { value: 'nasal', label: 'Nasal' },
    { value: 'full_face', label: 'Full Face' },
]

function styleLabel(style: string): string {
    return STYLE_OPTIONS.find((o) => o.value === style)?.label ?? style
}

function maskSummary(entry: MaskLogEntryResponse): string {
    const details = [styleLabel(entry.style)]
    if (entry.size) details.push(`size ${entry.size}`)
    return `${entry.brand} ${entry.model} (${details.join(', ')})`
}

const {
    data: maskData,
    loading: maskLoading,
    error: maskError,
    reload: reloadMasks,
} = useApiLoad(() => getMaskLog(), 'Failed to load mask log')

// API returns entries oldest-first; show most recent first like the rest of the page.
const masks = computed<MaskLogEntryResponse[]>(() => [...(maskData.value ?? [])].reverse())

// Device setting changes merged with mask log entries, most recent first.
type TimelineRow =
    | { source: 'device'; key: string; date: string; change: RxSettingChange }
    | { source: 'mask'; key: string; date: string; entry: MaskLogEntryResponse }

const timelineRows = computed<TimelineRow[]>(() => {
    const deviceRows: TimelineRow[] = (data.value?.changes?.changes ?? []).map((change, i) => ({
        source: 'device',
        key: `device-${change.date}-${change.device_id}-${change.key}-${i}`,
        date: change.date,
        change,
    }))
    const maskRows: TimelineRow[] = (maskData.value ?? []).map((entry) => ({
        source: 'mask',
        key: `mask-${entry.id}`,
        date: entry.start_date,
        entry,
    }))
    return [...deviceRows, ...maskRows].sort((a, b) => b.date.localeCompare(a.date))
})

// --- Add / edit form ---

const emptyForm = {
    brand: '',
    model: '',
    style: 'nasal' as MaskStyle,
    startDate: '',
    size: '',
    notes: '',
}
const form = ref({ ...emptyForm })
const editingId = ref<number | null>(null)
const saving = ref(false)
const maskActionError = ref<string | null>(null)

const formValid = computed(
    () =>
        form.value.brand.trim() !== '' &&
        form.value.model.trim() !== '' &&
        form.value.startDate !== '',
)

function resetForm() {
    form.value = { ...emptyForm }
    editingId.value = null
}

function startEdit(entry: MaskLogEntryResponse) {
    editingId.value = entry.id
    form.value = {
        brand: entry.brand,
        model: entry.model,
        style: STYLE_OPTIONS.some((o) => o.value === entry.style)
            ? (entry.style as MaskStyle)
            : 'nasal',
        startDate: entry.start_date,
        size: entry.size ?? '',
        notes: entry.notes ?? '',
    }
    maskActionError.value = null
}

async function handleSubmit() {
    if (!formValid.value) return
    saving.value = true
    maskActionError.value = null
    const body = {
        brand: form.value.brand.trim(),
        model: form.value.model.trim(),
        style: form.value.style,
        start_date: form.value.startDate,
        size: form.value.size.trim() || null,
        notes: form.value.notes.trim() || null,
    }
    try {
        if (editingId.value != null) {
            await updateMaskLogEntry(editingId.value, body)
        } else {
            await createMaskLogEntry(body)
        }
        resetForm()
        await reloadMasks()
    } catch (e: unknown) {
        maskActionError.value = e instanceof Error ? e.message : 'Failed to save mask entry'
    } finally {
        saving.value = false
    }
}

// --- Delete ---

const deleteTarget = ref<MaskLogEntryResponse | null>(null)
const deleteDialogVisible = ref(false)
const deleting = ref(false)

const deleteMessage = computed(() =>
    deleteTarget.value
        ? `Delete ${deleteTarget.value.brand} ${deleteTarget.value.model} from the mask log?`
        : '',
)

function startDelete(entry: MaskLogEntryResponse) {
    deleteTarget.value = entry
    deleteDialogVisible.value = true
    maskActionError.value = null
}

async function handleDelete() {
    if (!deleteTarget.value) return
    deleting.value = true
    try {
        await deleteMaskLogEntry(deleteTarget.value.id)
        if (editingId.value === deleteTarget.value.id) resetForm()
        await reloadMasks()
    } catch (e: unknown) {
        maskActionError.value = e instanceof Error ? e.message : 'Failed to delete mask entry'
    } finally {
        deleting.value = false
        deleteDialogVisible.value = false
        deleteTarget.value = null
    }
}
</script>

<style scoped>
.rx-history {
    max-width: 1200px;
}

.no-data {
    padding: 2rem;
    text-align: center;
    color: var(--color-muted-foreground);
}

.current-meta {
    display: flex;
    gap: 1.25rem;
    flex-wrap: wrap;
    font-size: 0.9rem;
    color: var(--color-muted-foreground);
    margin-bottom: 0.75rem;
}

.settings-pills {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.setting-pill {
    font-size: 0.78rem;
}

.mask-empty {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.mask-action-error {
    margin-top: 0.75rem;
    font-size: 0.875rem;
    color: var(--color-destructive);
}

.mask-form {
    margin-top: 1rem;
    padding-top: 1rem;
    border-top: 1px solid var(--color-border);
}

.mask-form h3 {
    font-size: 0.9rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
}

.mask-form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.75rem;
    margin-bottom: 0.75rem;
}

.mask-field {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}

.mask-label {
    font-size: 0.8rem;
    font-weight: 500;
}

.mask-form-actions {
    display: flex;
    gap: 0.5rem;
}
</style>
