<template>
    <div class="section-card">
        <h2>Mask Equipment</h2>

        <div v-if="maskLoading" class="loading-state">
            <Loader2 class="h-4 w-4 animate-spin" /> Loading mask log...
        </div>

        <ErrorState v-else-if="maskError" :message="maskError" :retry="reloadMasks" />

        <template v-else>
            <!-- Epoch cards (from device data) -->
            <div v-if="epochs.length > 0" class="epoch-list">
                <div
                    v-for="card in epochCards"
                    :key="`${card.epoch.device_id ?? 'none'}:${card.epoch.start_date}`"
                    class="epoch-card"
                >
                    <div class="epoch-header">
                        <div class="epoch-info">
                            <span class="epoch-style">{{ epochStyleLabel(card.epoch) }}</span>
                            <span class="epoch-dates">
                                {{ formatDateFull(card.epoch.start_date) }} –
                                {{ formatDateFull(card.epoch.end_date) }}
                            </span>
                            <span class="epoch-nights">{{ card.epoch.days_count }} nights</span>
                            <span v-if="card.epoch.device_name" class="epoch-device">
                                {{ card.epoch.device_name }}
                            </span>
                        </div>
                        <Button
                            v-if="canWrite"
                            size="sm"
                            variant="outline"
                            :disabled="saving"
                            @click="prefillFromEpoch(card.epoch)"
                        >
                            Add details
                        </Button>
                    </div>
                    <div v-if="card.entries.length > 0" class="epoch-entries">
                        <MaskEntryTable
                            :entries="card.entries"
                            :can-write="canWrite"
                            :saving="saving"
                            @edit="startEdit"
                            @delete="startDelete"
                        />
                    </div>
                </div>
            </div>

            <!-- Entries not matched to any epoch (and all entries when no epochs exist) -->
            <template v-if="otherEntries.length > 0">
                <h3 v-if="epochs.length > 0" class="other-entries-heading">Other entries</h3>
                <MaskEntryTable
                    :entries="otherEntries"
                    :can-write="canWrite"
                    :saving="saving"
                    @edit="startEdit"
                    @delete="startDelete"
                />
            </template>
            <p v-else-if="!masks.length" class="mask-empty">No mask equipment logged yet.</p>

            <p v-if="maskActionError" class="mask-action-error">{{ maskActionError }}</p>

            <form v-if="canWrite" ref="formRef" class="mask-form" @submit.prevent="handleSubmit">
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
                        <Select
                            :model-value="form.style || STYLE_UNSET"
                            @update:model-value="onStyleSelect"
                        >
                            <SelectTrigger class="w-full">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem :value="STYLE_UNSET">—</SelectItem>
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
                        <template v-if="effectiveSizeMode === 'catalog'">
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
                                v-if="form.style"
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
                    <Button type="submit" :disabled="saving">
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
import { computed, nextTick, ref } from 'vue'
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
    getMaskLog,
    createMaskLogEntry,
    updateMaskLogEntry,
    deleteMaskLogEntry,
} from '@/api/equipment'
import { useApiLoad } from '@/composables/useApiLoad'
import { useAuth } from '@/composables/useAuth'
import { formatDateFull } from '@/utils/formatting'
import type { MaskEpochResponse, MaskLogEntryResponse } from '@/types'
import DatePickerInput from '@/components/DatePickerInput.vue'
import DeleteConfirmDialog from '@/components/DeleteConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import MaskEntryTable from '@/components/MaskEntryTable.vue'
import {
    CUSTOM_VALUE,
    MASK_CATALOG,
    SIZES_BY_STYLE,
    STYLE_OPTIONS,
    findBrand,
    findModel,
    styleLabel,
    type MaskStyle,
} from '@/utils/maskOptions'

const props = withDefaults(defineProps<{ epochs?: MaskEpochResponse[] }>(), { epochs: () => [] })

const { canWrite } = useAuth()

const {
    data: maskData,
    loading: maskLoading,
    error: maskError,
    reload: reloadMasks,
} = useApiLoad(() => getMaskLog(), 'Failed to load mask log')

// API returns entries oldest-first; show most recent first.
const masks = computed<MaskLogEntryResponse[]>(() => [...(maskData.value ?? [])].reverse())

// Sentinel for the unset style Select option.
const STYLE_UNSET = '__style_none__'

function epochStyleLabel(epoch: MaskEpochResponse): string {
    // Device reported an unrecognized mask type — show it verbatim.
    return styleLabel(epoch.style) || epoch.mask_type
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
    style: '' as MaskStyle | '',
    startDate: '',
    size: '',
    notes: '',
}
const form = ref({ ...emptyForm })
const editingId = ref<number | null>(null)
const saving = ref(false)
const maskActionError = ref<string | null>(null)
const formRef = ref<HTMLFormElement | null>(null)

// --- Derived selects ---

const currentBrandModels = computed(() => findBrand(form.value.brand)?.models ?? [])

const currentSizes = computed(() => {
    const style = form.value.style
    return style ? (SIZES_BY_STYLE[style] ?? []) : []
})

// When style is unset, the catalog size list is empty and unhelpful — fall back to text input.
const effectiveSizeMode = computed<FieldMode>(() => (form.value.style ? sizeMode.value : 'custom'))

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

// --- Style cascade ---

function onStyleSelect(value: unknown) {
    if (value === STYLE_UNSET || value === '') {
        form.value.style = ''
    } else {
        const str = typeof value === 'string' ? value : ''
        form.value.style = STYLE_OPTIONS.some((o) => o.value === str) ? (str as MaskStyle) : ''
    }
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

// Populate brand/model/style/size form fields and their catalog/custom modes from an entry.
// startDate, notes, and editingId are left untouched — callers set those themselves.
function applyEntryToForm(entry: MaskLogEntryResponse) {
    const brand = entry.brand ?? ''
    const model = entry.model ?? ''
    const style = entry.style ?? ''

    brandMode.value = brand ? (findBrand(brand) ? 'catalog' : 'custom') : 'catalog'
    modelMode.value = brand && model ? (findModel(brand, model) ? 'catalog' : 'custom') : 'catalog'

    const entryStyle: MaskStyle | '' = STYLE_OPTIONS.some((o) => o.value === style)
        ? (style as MaskStyle)
        : ''

    if (!entry.size) {
        sizeMode.value = 'catalog'
    } else if (entryStyle && SIZES_BY_STYLE[entryStyle]?.includes(entry.size)) {
        sizeMode.value = 'catalog'
    } else {
        sizeMode.value = 'custom'
    }

    form.value.brand = brand
    form.value.model = model
    form.value.style = entryStyle
    form.value.size = entry.size ?? ''
}

function startEdit(entry: MaskLogEntryResponse) {
    editingId.value = entry.id
    maskActionError.value = null
    applyEntryToForm(entry)
    form.value.startDate = entry.start_date ?? ''
    form.value.notes = entry.notes ?? ''
}

// Return the most recent logged entry whose style matches epochStyle, for use as a template.
// "Most recent": greatest start_date among dated entries (ties broken by greatest id);
// falls back to greatest id among undated entries when no dated candidates exist.
function findTemplateEntry(epochStyle: MaskStyle): MaskLogEntryResponse | null {
    const candidates = masks.value.filter((e) => e.style === epochStyle)
    const dated = candidates.filter((e) => e.start_date !== null)
    if (dated.length > 0) {
        return dated.reduce((best, e) => {
            if (e.start_date! > best.start_date!) return e
            if (e.start_date! === best.start_date! && e.id > best.id) return e
            return best
        })
    }
    const undated = candidates.filter((e) => e.start_date === null)
    if (undated.length > 0) {
        return undated.reduce((best, e) => (e.id > best.id ? e : best))
    }
    return null
}

function prefillFromEpoch(epoch: MaskEpochResponse) {
    resetForm()
    maskActionError.value = null

    const epochStyle: MaskStyle | '' = STYLE_OPTIONS.some((o) => o.value === (epoch.style ?? ''))
        ? (epoch.style as MaskStyle)
        : ''

    // Trial periods mean the same physical mask often spans multiple device epochs.
    // Prefill equipment identity from the most recent same-style entry so the user
    // doesn't have to re-enter brand/model/size for each new epoch.
    if (epochStyle) {
        const template = findTemplateEntry(epochStyle)
        if (template) {
            applyEntryToForm(template)
        }
        // Device-reported style always wins; notes are entry-specific and must not carry over.
        form.value.style = epochStyle
    }

    form.value.startDate = epoch.start_date

    nextTick(() => {
        formRef.value?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
    })
}

async function handleSubmit() {
    saving.value = true
    maskActionError.value = null
    const body = {
        brand: form.value.brand.trim() || null,
        model: form.value.model.trim() || null,
        style: (form.value.style as MaskStyle) || null,
        start_date: form.value.startDate || null,
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

// --- Epoch entry grouping ---

// Single pass over entries: bucket each into its first matching epoch or the "other" list.
// Tie-break: an entry whose start_date falls inside multiple epochs (possible when two devices'
// date ranges overlap) lands in the FIRST epoch in chronological array order — deliberate,
// so entries do not duplicate across cards.
const epochGrouping = computed(() => {
    const byEpoch = new Map<MaskEpochResponse, MaskLogEntryResponse[]>(
        props.epochs.map((e) => [e, []]),
    )
    const other: MaskLogEntryResponse[] = []
    for (const entry of masks.value) {
        const matched = entry.start_date
            ? (props.epochs.find(
                  (e) => entry.start_date! >= e.start_date && entry.start_date! <= e.end_date,
              ) ?? null)
            : null
        if (matched !== null) {
            byEpoch.get(matched)!.push(entry)
        } else {
            other.push(entry)
        }
    }
    return { byEpoch, other }
})

// Flat list of {epoch, entries} cards for stable keying and single Map lookup per render.
const epochCards = computed(() =>
    props.epochs.map((epoch) => ({
        epoch,
        entries: epochGrouping.value.byEpoch.get(epoch) ?? [],
    })),
)

const otherEntries = computed(() => epochGrouping.value.other)

// --- Delete ---

const deleteTarget = ref<MaskLogEntryResponse | null>(null)
const deleteDialogVisible = ref(false)
const deleting = ref(false)

const deleteMessage = computed(() =>
    deleteTarget.value
        ? `Delete ${deleteTarget.value.brand ?? 'mask'} ${deleteTarget.value.model ?? 'entry'} from the mask log?`
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

.epoch-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-bottom: 1rem;
}

.epoch-card {
    border: 1px solid var(--color-border);
    border-radius: 0.5rem;
    padding: 0.75rem;
}

.epoch-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.25rem;
}

.epoch-info {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.5rem;
    font-size: 0.875rem;
}

.epoch-style {
    font-weight: 600;
}

.epoch-dates {
    color: var(--color-muted-foreground);
}

.epoch-nights {
    color: var(--color-muted-foreground);
    font-size: 0.8rem;
}

.epoch-device {
    color: var(--color-muted-foreground);
    font-size: 0.8rem;
    font-style: italic;
}

.epoch-entries {
    margin-top: 0.5rem;
    border-top: 1px solid var(--color-border);
    padding-top: 0.5rem;
}

.other-entries-heading {
    font-size: 0.875rem;
    font-weight: 600;
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    color: var(--color-muted-foreground);
}
</style>
