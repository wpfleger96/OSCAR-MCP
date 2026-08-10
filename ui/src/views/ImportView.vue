<template>
    <div class="import-view">
        <h1 class="page-title">Import Data</h1>

        <!-- Upload hero card -->
        <div class="hero-card">
            <!-- Profile selector (multiuser mode with multiple profiles) -->
            <div v-if="profiles.length > 1" class="profile-selector">
                <label for="import-profile" class="profile-selector-label"
                    >Import into profile</label
                >
                <select id="import-profile" v-model="selectedProfileId" class="field-select">
                    <option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name }}</option>
                </select>
            </div>

            <!-- Always-present hidden file input so fileInputRef is always bound -->
            <input
                ref="fileInputRef"
                type="file"
                webkitdirectory
                multiple
                class="hidden"
                @change="onFileChange"
            />

            <!-- idle: drop zone -->
            <div
                v-if="uploadPhase === 'idle'"
                class="drop-zone"
                :class="{ dragging: isDragging }"
                @click="fileInputRef?.click()"
                @dragover.prevent="isDragging = true"
                @dragleave.prevent="isDragging = false"
                @drop.prevent="onDrop"
            >
                <Upload class="drop-icon" />
                <p class="drop-text">Drop SD card folder here or click to browse</p>
                <p class="drop-text drop-subtext">Select the root folder of your CPAP SD card</p>
                <p v-if="dropError" class="error-text">{{ dropError }}</p>
            </div>

            <!-- selected: folder summary + structure indicator + actions -->
            <template v-else-if="uploadPhase === 'selected'">
                <div class="folder-info">
                    <Folder class="folder-info-icon" />
                    <span class="folder-name">{{ folderName }}</span>
                    <span class="folder-sep">·</span>
                    <span class="folder-meta">{{ fileEntries.length }} files</span>
                    <span class="folder-sep">·</span>
                    <span class="folder-meta">{{ formatBytes(totalSize) }}</span>
                </div>
                <p v-if="hasResMedStructure" class="structure-ok">
                    <CheckCircle2 class="h-4 w-4" />
                    ResMed SD card structure detected
                </p>
                <p v-else class="structure-warn">
                    <AlertTriangle class="h-4 w-4" />
                    Doesn't look like a ResMed SD card — import will still be attempted
                </p>
                <p v-if="precheckPending" class="folder-meta precheck-hint">
                    Checking for files already on server…
                </p>
                <template v-else-if="skippedCount > 0">
                    <p class="structure-ok">
                        <CheckCircle2 class="h-4 w-4" />
                        <template v-if="forceUploadAll">
                            Uploading all {{ fileEntries.length }} files (dedupe skipped)
                        </template>
                        <template v-else>
                            {{ skippedCount }} of {{ fileEntries.length }} files already on server —
                            will upload {{ uploadCount }} files ({{ formatBytes(uploadBytes) }})
                        </template>
                    </p>
                    <label class="dedupe-skip-label">
                        <input
                            v-model="forceUploadAll"
                            type="checkbox"
                            class="dedupe-skip-checkbox"
                        />
                        Upload all files (skip dedupe)
                    </label>
                </template>
                <div class="card-actions">
                    <Button variant="outline" @click="resetUpload">Change folder</Button>
                    <Button @click="handleImport">
                        <Upload class="mr-2 h-4 w-4" />
                        Import
                    </Button>
                </div>
            </template>

            <!-- uploading: progress bar -->
            <template v-else-if="uploadPhase === 'uploading'">
                <p v-if="skipSummary" class="skip-summary">
                    Skipped {{ skipSummary.count }} files already on server ({{
                        formatBytes(skipSummary.bytes)
                    }}
                    saved)
                </p>
                <p class="progress-label">
                    {{ batchLabel ?? 'Uploading' }}… {{ uploadProgress }}%
                    <span v-if="uploadTotal > 0">
                        ({{ formatBytes(uploadLoaded) }} / {{ formatBytes(uploadTotal) }})
                    </span>
                </p>
                <div class="progress-track">
                    <div class="progress-fill" :style="{ width: uploadProgress + '%' }" />
                </div>
            </template>

            <!-- error: message + retry actions -->
            <template v-else-if="uploadPhase === 'error'">
                <p class="error-text">{{ importError }}</p>
                <div class="card-actions">
                    <Button variant="outline" @click="resetUpload">Change folder</Button>
                    <Button @click="handleImport">Try again</Button>
                </div>
            </template>
        </div>

        <!-- Active / recent import jobs -->
        <ImportJobsPanel :jobs="importJobs" @cancel="handleCancelImportJob" />
    </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import type { PipelineJobStatus } from '@/types'
import {
    importFiles,
    precheckFiles,
    isAnchorFile,
    type FileEntry,
    type ChunkedImportProgress,
} from '@/api/import'
import { getImportJobs, cancelImport, ACTIVE_PIPELINE_STAGES } from '@/api/importJobs'
import { cancelAnalysisJob } from '@/api/analysis'
import { formatBytes } from '@/utils/formatting'
import { Button } from '@/components/ui/button'
import ImportJobsPanel from '@/components/ImportJobsPanel.vue'
import { Upload, Folder, CheckCircle2, AlertTriangle } from '@lucide/vue'
import { useAuth } from '@/composables/useAuth'

// ---------------------------------------------------------------------------
// Profile selection
// ---------------------------------------------------------------------------

const { profiles, activeProfileId } = useAuth()
const selectedProfileId = ref<number | null>(activeProfileId.value)

// ---------------------------------------------------------------------------
// Upload flow state
// ---------------------------------------------------------------------------

type UploadPhase = 'idle' | 'selected' | 'uploading' | 'error'

const uploadPhase = ref<UploadPhase>('idle')
const fileEntries = ref<FileEntry[]>([])
const folderName = ref('')
const totalSize = ref(0)
const uploadProgress = ref(0)
const uploadLoaded = ref(0)
const uploadTotal = ref(0)
const importError = ref<string | null>(null)
const isDragging = ref(false)
const dropError = ref<string | null>(null)
const batchLabel = ref<string | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const precheckPending = ref(false)
const skippablePaths = ref<Set<string>>(new Set())
// The profile the current skippablePaths was computed for; undefined = no valid result.
const precheckProfileId = ref<number | null | undefined>(undefined)
const forceUploadAll = ref(false)
const skipSummary = ref<{ count: number; bytes: number } | null>(null)
let precheckPromise: Promise<void> | null = null
let precheckGeneration = 0

const hasResMedStructure = computed(
    () =>
        fileEntries.value.some((e) => /(^|\/)STR\.edf$/i.test(e.path)) &&
        fileEntries.value.some((e) => /(^|\/)DATALOG\//i.test(e.path)),
)

const skippedEntries = computed(() =>
    fileEntries.value.filter((e) => skippablePaths.value.has(e.path) && !isAnchorFile(e)),
)
const skippedCount = computed(() => skippedEntries.value.length)
const skippedBytes = computed(() => skippedEntries.value.reduce((s, e) => s + e.file.size, 0))
const uploadCount = computed(() => fileEntries.value.length - skippedCount.value)
const uploadBytes = computed(() => totalSize.value - skippedBytes.value)

function runPrecheck(): void {
    precheckGeneration++
    const gen = precheckGeneration
    // Clear stale data immediately so it is never used while a re-check is pending.
    skippablePaths.value = new Set()
    precheckProfileId.value = undefined
    if (!hasResMedStructure.value) {
        precheckPending.value = false
        precheckPromise = null
        return
    }
    precheckPending.value = true
    // Capture the profile now; we tag the result with it so handleImport can
    // reject a set computed for a different profile.
    const profile = selectedProfileId.value
    precheckPromise = precheckFiles(fileEntries.value, profile ?? undefined).then((result) => {
        if (gen === precheckGeneration) {
            skippablePaths.value = result
            precheckProfileId.value = profile
            precheckPending.value = false
        }
    })
}

watch(selectedProfileId, () => {
    if (uploadPhase.value === 'selected' || uploadPhase.value === 'error') {
        runPrecheck()
    }
})

function setEntries(entries: FileEntry[]) {
    dropError.value = null
    fileEntries.value = entries
    const firstWithSlash = entries.find((e) => e.path.includes('/'))
    folderName.value = firstWithSlash ? firstWithSlash.path.split('/')[0] : 'Selected files'
    totalSize.value = entries.reduce((sum, e) => sum + e.file.size, 0)
    uploadPhase.value = 'selected'
    void runPrecheck()
}

function onFileChange(event: Event) {
    const input = event.target as HTMLInputElement
    if (!input.files || input.files.length === 0) return
    const entries: FileEntry[] = Array.from(input.files).map((file) => ({
        file,
        path: file.webkitRelativePath || file.name,
    }))
    setEntries(entries)
}

// FileSystem API traversal — readEntries() yields at most ~100 entries per call,
// so we loop until it returns an empty batch.
function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
    return new Promise((resolve, reject) => {
        const all: FileSystemEntry[] = []
        const next = () =>
            reader.readEntries(
                (batch) => (batch.length ? (all.push(...batch), next()) : resolve(all)),
                reject,
            )
        next()
    })
}

function traverseEntry(entry: FileSystemEntry): Promise<FileEntry[]> {
    if (entry.isFile) {
        const fileEntry = entry as FileSystemFileEntry
        return new Promise((resolve, reject) => {
            fileEntry.file(
                (file) => resolve([{ file, path: entry.fullPath.replace(/^\//, '') }]),
                reject,
            )
        })
    }
    if (entry.isDirectory) {
        const dirEntry = entry as FileSystemDirectoryEntry
        return readAllEntries(dirEntry.createReader()).then((children) =>
            Promise.all(children.map(traverseEntry)).then((nested) => nested.flat()),
        )
    }
    return Promise.resolve([])
}

async function onDrop(event: DragEvent) {
    isDragging.value = false
    dropError.value = null
    const items = event.dataTransfer?.items
    if (items && items.length > 0 && items[0].webkitGetAsEntry !== undefined) {
        const entryPromises: Promise<FileEntry[]>[] = []
        for (let i = 0; i < items.length; i++) {
            const entry = items[i].webkitGetAsEntry?.()
            if (entry) entryPromises.push(traverseEntry(entry))
        }
        try {
            const allEntries = (await Promise.all(entryPromises)).flat()
            if (allEntries.length > 0) setEntries(allEntries)
        } catch {
            dropError.value = 'Could not read the dropped folder — try the folder picker instead.'
        }
    } else {
        const files = event.dataTransfer?.files
        if (files && files.length > 0) {
            const entries: FileEntry[] = Array.from(files).map((file) => ({
                file,
                path: file.webkitRelativePath || file.name,
            }))
            setEntries(entries)
        }
    }
}

async function handleImport() {
    // Re-entrancy guard: a second click while uploading is a no-op.
    // The synchronous set closes the race before any await.
    if (uploadPhase.value === 'uploading') return
    uploadPhase.value = 'uploading'

    // Capture the profile at entry so a mid-await selector change can't
    // silently apply the wrong skip set or drift the upload's profile_id.
    const profileId = selectedProfileId.value

    // Await any in-flight precheck (bounded by 5 s axios timeout; never rejects).
    if (precheckPromise !== null) {
        await precheckPromise
    }

    // The skip set is only usable when it was computed for the profile we're
    // importing into; a mismatch (profile switched during the await) fails open.
    const skipSetValid = !forceUploadAll.value && precheckProfileId.value === profileId

    const skipped = skipSetValid
        ? fileEntries.value.filter((e) => skippablePaths.value.has(e.path) && !isAnchorFile(e))
        : []
    skipSummary.value =
        skipped.length > 0
            ? { count: skipped.length, bytes: skipped.reduce((s, e) => s + e.file.size, 0) }
            : null

    const entriesToUpload =
        skipSetValid && skippablePaths.value.size > 0
            ? fileEntries.value.filter((e) => !skippablePaths.value.has(e.path) || isAnchorFile(e))
            : fileEntries.value

    // Do NOT call setActiveProfile() here — it increments profileKey and
    // would unmount this view before the upload begins.
    uploadProgress.value = 0
    uploadLoaded.value = 0
    uploadTotal.value = 0
    importError.value = null
    batchLabel.value = null

    const onProgress = (progress: ChunkedImportProgress) => {
        uploadLoaded.value = progress.loaded
        uploadTotal.value = progress.total
        uploadProgress.value =
            progress.total > 0 ? Math.round((progress.loaded / progress.total) * 100) : 0
        batchLabel.value =
            progress.totalBatches > 1
                ? `Uploading batch ${progress.batchIndex} of ${progress.totalBatches}`
                : null
    }

    try {
        // Pass captured profileId to avoid drift if the selector changes mid-upload.
        await importFiles(entriesToUpload, onProgress, profileId ?? undefined)
        resetUpload()
        void fetchImportJobs()
    } catch (e: unknown) {
        importError.value = e instanceof Error ? e.message : 'Import failed'
        uploadPhase.value = 'error'
    }
}

function resetUpload() {
    precheckGeneration++
    uploadPhase.value = 'idle'
    fileEntries.value = []
    folderName.value = ''
    totalSize.value = 0
    uploadProgress.value = 0
    uploadLoaded.value = 0
    uploadTotal.value = 0
    importError.value = null
    batchLabel.value = null
    skippablePaths.value = new Set()
    precheckProfileId.value = undefined
    forceUploadAll.value = false
    precheckPending.value = false
    precheckPromise = null
    skipSummary.value = null
    if (fileInputRef.value) fileInputRef.value.value = ''
}

// ---------------------------------------------------------------------------
// Import jobs polling
// ---------------------------------------------------------------------------

const importJobs = ref<PipelineJobStatus[]>([])
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollStopped = false

async function fetchImportJobs() {
    try {
        const { jobs } = await getImportJobs()
        importJobs.value = jobs
        if (jobs.some((j) => ACTIVE_PIPELINE_STAGES.has(j.stage))) {
            schedulePoll()
        }
    } catch {
        if (importJobs.value.some((j) => ACTIVE_PIPELINE_STAGES.has(j.stage))) {
            schedulePoll()
        }
    }
}

function schedulePoll() {
    if (pollStopped || pollTimer !== null) return
    pollTimer = setTimeout(async () => {
        pollTimer = null
        if (pollStopped) return
        await fetchImportJobs()
    }, 3000)
}

async function handleCancelImportJob(job: PipelineJobStatus) {
    try {
        if ((job.stage === 'analysis_queued' || job.stage === 'analyzing') && job.analysis_job_id) {
            await cancelAnalysisJob(job.analysis_job_id)
        } else {
            await cancelImport(job.job_id)
        }
    } catch {
        /* job may already be terminal, reaped, or returned 409 */
    } finally {
        void fetchImportJobs()
    }
}

onMounted(() => {
    void fetchImportJobs()
})

onUnmounted(() => {
    // Invalidate any in-flight precheck so its .then() doesn't write to
    // refs of a destroyed component instance.
    precheckGeneration++
    precheckPromise = null
    pollStopped = true
    if (pollTimer) clearTimeout(pollTimer)
})
</script>

<style scoped>
.import-view {
    max-width: 800px;
    margin: 0 auto;
    padding: 1.5rem;
}

/* ---- Profile selector ---- */

.profile-selector {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--color-border);
}

.profile-selector-label {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--color-foreground);
    white-space: nowrap;
}

/* ---- Hero card ---- */

.hero-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 1.5rem;
    background: var(--color-card);
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-bottom: 1.5rem;
}

/* ---- Drop zone ---- */

.hidden {
    display: none;
}

.drop-zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    border: 2px dashed var(--color-border);
    border-radius: 8px;
    padding: 2.5rem 1rem;
    cursor: pointer;
    transition:
        border-color 0.15s,
        background 0.15s;
    background: transparent;
}

.drop-zone:hover,
.drop-zone.dragging {
    border-color: var(--color-primary);
    background: var(--color-accent);
}

.drop-icon {
    width: 2rem;
    height: 2rem;
    color: var(--color-muted-foreground);
}

.drop-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    text-align: center;
}

.drop-subtext {
    font-size: 0.8rem;
    opacity: 0.7;
}

/* ---- Selected phase ---- */

.folder-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.folder-info-icon {
    width: 1.25rem;
    height: 1.25rem;
    color: var(--color-muted-foreground);
    flex-shrink: 0;
}

.folder-name {
    font-weight: 600;
    font-size: 0.95rem;
    color: var(--color-foreground);
}

.folder-sep {
    color: var(--color-muted-foreground);
}

.folder-meta {
    font-size: 0.85rem;
    color: var(--color-muted-foreground);
}

.structure-ok {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: var(--color-success);
    margin: 0;
}

.structure-warn {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: color-mix(in srgb, var(--color-warning) 80%, var(--color-foreground));
    margin: 0;
}

.precheck-hint {
    font-style: italic;
}

.dedupe-skip-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
    cursor: pointer;
}

.dedupe-skip-checkbox {
    cursor: pointer;
}

/* ---- Card action row (selected, error) ---- */

.card-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
    padding-top: 0.25rem;
}

/* ---- Upload progress ---- */

.skip-summary {
    font-size: 0.85rem;
    color: var(--color-success);
    margin: 0;
}

.progress-label {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
    margin: 0;
}

.progress-track {
    height: 0.5rem;
    border-radius: 9999px;
    background: var(--color-muted);
    overflow: hidden;
}

.progress-fill {
    height: 100%;
    border-radius: 9999px;
    background: var(--color-primary);
    transition: width 0.2s;
}

/* ---- Error ---- */

.error-text {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin: 0;
}
</style>
