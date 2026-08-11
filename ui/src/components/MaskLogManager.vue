<template>
    <div class="section-card">
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
                            <TableCell class="whitespace-nowrap">{{ entry.size ?? '—' }}</TableCell>
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
                    <!-- Brand -->
                    <div class="mask-field">
                        <label class="mask-label">Brand</label>
                        <template v-if="brandMode === 'catalog'">
                            <Select
                                :model-value="form.brand || undefined"
                                @update:model-value="onBrandSelect"
                            >
                                <SelectTrigger class="w-full">
                                    <SelectValue placeholder="Select brand" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem
                                        v-for="b in MASK_CATALOG"
                                        :key="b.name"
                                        :value="b.name"
                                    >
                                        {{ b.name }}
                                    </SelectItem>
                                    <SelectItem :value="CUSTOM_VALUE">Custom…</SelectItem>
                                </SelectContent>
                            </Select>
                        </template>
                        <template v-else>
                            <input
                                v-model="form.brand"
                                type="text"
                                class="field-input"
                                placeholder="Brand name"
                                required
                                maxlength="100"
                                :disabled="saving"
                            />
                            <button
                                type="button"
                                class="use-list-link"
                                @click="switchBrandToCatalog"
                            >
                                Use list
                            </button>
                        </template>
                    </div>

                    <!-- Model -->
                    <div class="mask-field">
                        <label class="mask-label">Model</label>
                        <template
                            v-if="modelMode === 'catalog' && brandMode === 'catalog' && form.brand"
                        >
                            <Select
                                :model-value="form.model || undefined"
                                @update:model-value="onModelSelect"
                            >
                                <SelectTrigger class="w-full">
                                    <SelectValue placeholder="Select model" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem
                                        v-for="m in currentBrandModels"
                                        :key="m.name"
                                        :value="m.name"
                                    >
                                        {{ m.name }}
                                    </SelectItem>
                                    <SelectItem :value="CUSTOM_VALUE">Custom…</SelectItem>
                                </SelectContent>
                            </Select>
                        </template>
                        <template v-else>
                            <input
                                v-model="form.model"
                                type="text"
                                class="field-input"
                                placeholder="e.g. AirFit P10"
                                required
                                maxlength="150"
                                :disabled="saving"
                            />
                            <button
                                v-if="brandMode === 'catalog' && form.brand"
                                type="button"
                                class="use-list-link"
                                @click="switchModelToCatalog"
                            >
                                Use list
                            </button>
                        </template>
                    </div>

                    <!-- Style -->
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

                    <!-- Start Date -->
                    <div class="mask-field">
                        <label class="mask-label">Start Date</label>
                        <DatePickerInput v-model="form.startDate" />
                    </div>

                    <!-- Size -->
                    <div class="mask-field">
                        <label class="mask-label">Size</label>
                        <template v-if="sizeMode === 'catalog'">
                            <Select
                                :model-value="form.size || SIZE_NONE"
                                @update:model-value="onSizeSelect"
                            >
                                <SelectTrigger class="w-full">
                                    <SelectValue placeholder="None" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem :value="SIZE_NONE">None</SelectItem>
                                    <SelectItem v-for="s in currentSizes" :key="s" :value="s">
                                        {{ s }}
                                    </SelectItem>
                                    <SelectItem :value="CUSTOM_VALUE">Custom…</SelectItem>
                                </SelectContent>
                            </Select>
                        </template>
                        <template v-else>
                            <input
                                v-model="form.size"
                                type="text"
                                class="field-input"
                                placeholder="Optional"
                                maxlength="50"
                                :disabled="saving"
                            />
                            <button
                                type="button"
                                class="use-list-link"
                                @click="switchSizeToCatalog"
                            >
                                Use list
                            </button>
                        </template>
                    </div>

                    <!-- Notes -->
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
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Loader2 } from '@lucide/vue'
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
import {
    getMaskLog,
    createMaskLogEntry,
    updateMaskLogEntry,
    deleteMaskLogEntry,
} from '@/api/equipment'
import { useApiLoad } from '@/composables/useApiLoad'
import { useAuth } from '@/composables/useAuth'
import { formatDateFull } from '@/utils/formatting'
import type { MaskLogEntryResponse } from '@/types'
import DatePickerInput from '@/components/DatePickerInput.vue'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import {
    CUSTOM_VALUE,
    MASK_CATALOG,
    SIZES_BY_STYLE,
    findBrand,
    findModel,
    type MaskStyle,
} from '@/utils/maskOptions'

const { canWrite } = useAuth()

const {
    data: maskData,
    loading: maskLoading,
    error: maskError,
    reload: reloadMasks,
} = useApiLoad(() => getMaskLog(), 'Failed to load mask log')

// API returns entries oldest-first; show most recent first.
const masks = computed<MaskLogEntryResponse[]>(() => [...(maskData.value ?? [])].reverse())

// --- Style options (Nasal first, then Full Face, then Pillows) ---

const STYLE_OPTIONS: { value: MaskStyle; label: string }[] = [
    { value: 'nasal', label: 'Nasal' },
    { value: 'full_face', label: 'Full Face' },
    { value: 'pillows', label: 'Pillows' },
]

function styleLabel(style: string): string {
    return STYLE_OPTIONS.find((o) => o.value === style)?.label ?? style
}

// --- Per-field catalog/custom mode ---

type FieldMode = 'catalog' | 'custom'

const brandMode = ref<FieldMode>('catalog')
const modelMode = ref<FieldMode>('catalog')
const sizeMode = ref<FieldMode>('catalog')

// Sentinel used as SelectItem value for the "None" size option.
const SIZE_NONE = '__size_none__'

// --- Form state ---

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

// --- Derived selects ---

const currentBrandModels = computed(() => findBrand(form.value.brand)?.models ?? [])

const currentSizes = computed(() => SIZES_BY_STYLE[form.value.style] ?? [])

// --- Brand cascade ---

function onBrandSelect(value: unknown) {
    if (value === CUSTOM_VALUE) {
        brandMode.value = 'custom'
        form.value.brand = ''
        modelMode.value = 'custom'
        form.value.model = ''
    } else {
        form.value.brand = typeof value === 'string' ? value : ''
        modelMode.value = 'catalog'
        form.value.model = ''
    }
    form.value.size = ''
    sizeMode.value = 'catalog'
}

function switchBrandToCatalog() {
    brandMode.value = 'catalog'
    form.value.brand = ''
    modelMode.value = 'catalog'
    form.value.model = ''
    form.value.size = ''
    sizeMode.value = 'catalog'
}

// --- Model cascade ---

function onModelSelect(value: unknown) {
    if (value === CUSTOM_VALUE) {
        modelMode.value = 'custom'
        form.value.model = ''
    } else {
        form.value.model = typeof value === 'string' ? value : ''
        const found = findModel(form.value.brand, form.value.model)
        if (found) form.value.style = found.style
    }
    form.value.size = ''
    sizeMode.value = 'catalog'
}

function switchModelToCatalog() {
    modelMode.value = 'catalog'
    form.value.model = ''
    form.value.size = ''
    sizeMode.value = 'catalog'
}

// --- Size cascade ---

function onSizeSelect(value: unknown) {
    if (value === CUSTOM_VALUE) {
        sizeMode.value = 'custom'
        form.value.size = ''
    } else if (value === SIZE_NONE) {
        form.value.size = ''
    } else {
        form.value.size = typeof value === 'string' ? value : ''
    }
}

function switchSizeToCatalog() {
    sizeMode.value = 'catalog'
    form.value.size = ''
}

// --- Form lifecycle ---

function resetForm() {
    form.value = { ...emptyForm }
    editingId.value = null
    brandMode.value = 'catalog'
    modelMode.value = 'catalog'
    sizeMode.value = 'catalog'
}

function startEdit(entry: MaskLogEntryResponse) {
    editingId.value = entry.id
    maskActionError.value = null

    brandMode.value = findBrand(entry.brand) ? 'catalog' : 'custom'
    modelMode.value = findModel(entry.brand, entry.model) ? 'catalog' : 'custom'

    const entryStyle = STYLE_OPTIONS.some((o) => o.value === entry.style)
        ? (entry.style as MaskStyle)
        : 'nasal'

    if (!entry.size) {
        sizeMode.value = 'catalog'
    } else if (SIZES_BY_STYLE[entryStyle]?.includes(entry.size)) {
        sizeMode.value = 'catalog'
    } else {
        sizeMode.value = 'custom'
    }

    form.value = {
        brand: entry.brand,
        model: entry.model,
        style: entryStyle,
        startDate: entry.start_date,
        size: entry.size ?? '',
        notes: entry.notes ?? '',
    }
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

.use-list-link {
    align-self: flex-start;
    font-size: 0.75rem;
    color: var(--color-primary);
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
}

.use-list-link:hover {
    opacity: 0.8;
}
</style>
