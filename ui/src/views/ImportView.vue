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
                <select
                    id="import-profile"
                    v-model="selectedProfileId"
                    class="profile-selector-select"
                >
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
                <p class="progress-label">
                    Uploading… {{ uploadProgress }}%
                    <span v-if="uploadTotal > 0">
                        ({{ formatBytes(uploadLoaded) }} / {{ formatBytes(uploadTotal) }})
                    </span>
                </p>
                <div class="progress-track">
                    <div class="progress-fill" :style="{ width: uploadProgress + '%' }" />
                </div>
            </template>

            <!-- processing: spinner with live status -->
            <template v-else-if="uploadPhase === 'processing'">
                <div class="processing-row">
                    <Loader2 class="h-6 w-6 animate-spin text-muted-foreground" />
                    <span class="processing-text">{{ processingMessage }}</span>
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

            <!-- done: results panel -->
            <ImportResultsPanel
                v-else-if="uploadPhase === 'done' && importResult"
                :result="importResult"
                @reset="resetUpload"
            />
        </div>

        <!-- Server path section — localhost only; server enforces 403 otherwise -->
        <details v-if="isLocalhost" class="path-section">
            <summary class="path-summary">Import from server path (localhost)</summary>
            <div class="path-content">
                <!-- idle / detecting: path input -->
                <template v-if="pathPhase === 'idle' || pathPhase === 'detecting'">
                    <div class="path-row">
                        <input
                            v-model="sourcePath"
                            type="text"
                            placeholder="/mnt/sd-card"
                            class="path-input"
                            @keydown.enter="handleDetect"
                        />
                        <Button
                            :disabled="!sourcePath || pathPhase === 'detecting'"
                            @click="handleDetect"
                        >
                            <Loader2
                                v-if="pathPhase === 'detecting'"
                                class="mr-2 h-4 w-4 animate-spin"
                            />
                            Detect Sources
                        </Button>
                    </div>
                    <p v-if="detectError" class="error-text">{{ detectError }}</p>
                    <p v-if="noSourcesDetected" class="path-no-sources">
                        No CPAP data sources found at that path.
                    </p>
                </template>

                <!-- detected / importing / error: source cards with checkboxes -->
                <template
                    v-else-if="
                        pathPhase === 'detected' ||
                        pathPhase === 'importing' ||
                        pathPhase === 'error'
                    "
                >
                    <div class="detected-sources">
                        <label
                            v-for="(src, i) in detectedSources"
                            :key="i"
                            class="source-card source-card-selectable"
                            :class="{ 'source-card-checked': selectedSources.has(i) }"
                        >
                            <input
                                type="checkbox"
                                :checked="selectedSources.has(i)"
                                class="source-checkbox"
                                @change="toggleSource(i)"
                            />
                            <div class="source-card-body">
                                <div class="source-parser">{{ src.parser_name }}</div>
                                <div v-if="src.device_serial" class="source-meta">
                                    Serial: {{ src.device_serial }}
                                </div>
                                <div v-if="src.data_root" class="source-meta">
                                    Data root: {{ src.data_root }}
                                </div>
                                <div class="source-meta source-path">{{ src.root_path }}</div>
                            </div>
                        </label>
                    </div>
                    <div v-if="pathPhase === 'importing'" class="processing-row">
                        <Loader2 class="h-5 w-5 animate-spin text-muted-foreground" />
                        <span class="processing-text">{{ processingMessage }}</span>
                    </div>
                    <p v-if="pathImportError" class="error-text">{{ pathImportError }}</p>
                    <div class="card-actions">
                        <Button variant="outline" @click="resetPath">Change path</Button>
                        <Button
                            :disabled="selectedSources.size === 0 || pathPhase === 'importing'"
                            @click="handlePathImport"
                        >
                            <Loader2
                                v-if="pathPhase === 'importing'"
                                class="mr-2 h-4 w-4 animate-spin"
                            />
                            Import Selected ({{ selectedSources.size }})
                        </Button>
                    </div>
                </template>

                <!-- done: results panel -->
                <ImportResultsPanel
                    v-else-if="pathPhase === 'done' && pathImportResult"
                    :result="pathImportResult"
                    @reset="resetPath"
                />
            </div>
        </details>
    </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { AxiosProgressEvent } from 'axios'
import type { ImportSource, ImportResult } from '@/types'
import { detectSources, importFiles, importFromPath, type FileEntry } from '@/api/import'
import { connectImportProgress } from '@/api/sse'
import { formatBytes } from '@/utils/formatting'
import { Button } from '@/components/ui/button'
import ImportResultsPanel from '@/components/ImportResultsPanel.vue'
import { Upload, Folder, Loader2, CheckCircle2, AlertTriangle } from '@lucide/vue'
import { useAuth } from '@/composables/useAuth'

// UI-only gate; server enforces 403 on non-localhost requests
const isLocalhost =
    window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'

// ---------------------------------------------------------------------------
// Profile selection
// ---------------------------------------------------------------------------

const { profiles, activeProfileId, setActiveProfile } = useAuth()
const selectedProfileId = ref<number | null>(activeProfileId.value)

// ---------------------------------------------------------------------------
// Upload flow state
// ---------------------------------------------------------------------------

type UploadPhase = 'idle' | 'selected' | 'uploading' | 'processing' | 'error' | 'done'

const uploadPhase = ref<UploadPhase>('idle')
const fileEntries = ref<FileEntry[]>([])
const folderName = ref('')
const totalSize = ref(0)
const uploadProgress = ref(0)
const uploadLoaded = ref(0)
const uploadTotal = ref(0)
const importError = ref<string | null>(null)
const importResult = ref<ImportResult | null>(null)
const processingMessage = ref('Processing files...')
const isDragging = ref(false)
const dropError = ref<string | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

const hasResMedStructure = computed(
    () =>
        fileEntries.value.some((e) => /(^|\/)STR\.edf$/i.test(e.path)) &&
        fileEntries.value.some((e) => /(^|\/)DATALOG\//i.test(e.path)),
)

function setEntries(entries: FileEntry[]) {
    dropError.value = null
    fileEntries.value = entries
    const firstWithSlash = entries.find((e) => e.path.includes('/'))
    folderName.value = firstWithSlash ? firstWithSlash.path.split('/')[0] : 'Selected files'
    totalSize.value = entries.reduce((sum, e) => sum + e.file.size, 0)
    uploadPhase.value = 'selected'
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
    // Switch active profile if the user selected a different one.
    // Abort entirely on failure — writing health data to the wrong profile silently
    // is worse than a visible error.
    if (selectedProfileId.value !== null && selectedProfileId.value !== activeProfileId.value) {
        try {
            await setActiveProfile(selectedProfileId.value)
        } catch {
            importError.value =
                'Could not switch to the selected profile. Please refresh and try again.'
            return
        }
    }

    uploadPhase.value = 'uploading'
    uploadProgress.value = 0
    uploadLoaded.value = 0
    uploadTotal.value = 0
    importError.value = null

    const onProgress = (event: AxiosProgressEvent) => {
        if (event.total) {
            uploadLoaded.value = event.loaded
            uploadTotal.value = event.total
            uploadProgress.value = Math.round((event.loaded / event.total) * 100)
            if (event.loaded >= event.total) uploadPhase.value = 'processing'
        }
    }

    try {
        const { job_id } = await importFiles(fileEntries.value, onProgress)
        uploadPhase.value = 'processing'
        processingMessage.value = 'Starting import...'

        connectImportProgress(job_id, {
            onProgress: (data) => {
                processingMessage.value = data.message
            },
            onComplete: (data) => {
                importResult.value = data.result as ImportResult
                uploadPhase.value = 'done'
            },
            onError: (data) => {
                importError.value = data.message
                uploadPhase.value = 'error'
            },
        })
    } catch (e: unknown) {
        importError.value = e instanceof Error ? e.message : 'Import failed'
        uploadPhase.value = 'error'
    }
}

function resetUpload() {
    uploadPhase.value = 'idle'
    fileEntries.value = []
    folderName.value = ''
    totalSize.value = 0
    uploadProgress.value = 0
    uploadLoaded.value = 0
    uploadTotal.value = 0
    importError.value = null
    importResult.value = null
    if (fileInputRef.value) fileInputRef.value.value = ''
}

// ---------------------------------------------------------------------------
// Server-path flow state
// ---------------------------------------------------------------------------

type PathPhase = 'idle' | 'detecting' | 'detected' | 'importing' | 'error' | 'done'

const pathPhase = ref<PathPhase>('idle')
const sourcePath = ref('')
const detectedSources = ref<ImportSource[]>([])
const selectedSources = ref<Set<number>>(new Set())
const noSourcesDetected = ref(false)
const detectError = ref<string | null>(null)
const pathImportError = ref<string | null>(null)
const pathImportResult = ref<ImportResult | null>(null)

function toggleSource(i: number) {
    const next = new Set(selectedSources.value)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    selectedSources.value = next
}

async function handleDetect() {
    if (!sourcePath.value) return
    pathPhase.value = 'detecting'
    detectError.value = null
    noSourcesDetected.value = false

    try {
        const sources = await detectSources({ path: sourcePath.value })
        detectedSources.value = sources
        if (sources.length === 0) {
            noSourcesDetected.value = true
            pathPhase.value = 'idle'
        } else {
            selectedSources.value = new Set(sources.map((_, i) => i))
            pathPhase.value = 'detected'
        }
    } catch (e: unknown) {
        detectError.value = e instanceof Error ? e.message : 'Detection failed'
        pathPhase.value = 'idle'
    }
}

async function handlePathImport() {
    const selected = detectedSources.value.filter((_, i) => selectedSources.value.has(i))
    if (selected.length === 0) return
    pathPhase.value = 'importing'
    pathImportError.value = null
    processingMessage.value = 'Starting import...'

    try {
        const { job_id } = await importFromPath({ sources: selected })

        connectImportProgress(job_id, {
            onProgress: (data) => {
                processingMessage.value = data.message
            },
            onComplete: (data) => {
                pathImportResult.value = data.result as ImportResult
                pathPhase.value = 'done'
            },
            onError: (data) => {
                pathImportError.value = data.message
                pathPhase.value = 'error'
            },
        })
    } catch (e: unknown) {
        pathImportError.value = e instanceof Error ? e.message : 'Import failed'
        pathPhase.value = 'error'
    }
}

function resetPath() {
    pathPhase.value = 'idle'
    sourcePath.value = ''
    detectedSources.value = []
    selectedSources.value = new Set()
    noSourcesDetected.value = false
    detectError.value = null
    pathImportError.value = null
    pathImportResult.value = null
}
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

.profile-selector-select {
    height: 2.25rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0 0.75rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    outline: none;
    cursor: pointer;
    transition: border-color 0.15s;
}

.profile-selector-select:focus {
    border-color: var(--color-primary);
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

/* ---- Card action row (selected, error) ---- */

.card-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
    padding-top: 0.25rem;
}

/* ---- Upload progress ---- */

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

/* ---- Processing ---- */

.processing-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0;
}

.processing-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

/* ---- Error ---- */

.error-text {
    font-size: 0.875rem;
    color: var(--color-destructive);
    margin: 0;
}

/* ---- Server-path section ---- */

.path-section {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: var(--color-card);
    overflow: hidden;
}

.path-summary {
    padding: 0.75rem 1rem;
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--color-muted-foreground);
    cursor: pointer;
    user-select: none;
    list-style: none;
}

.path-summary::-webkit-details-marker {
    display: none;
}

.path-summary::before {
    content: '▶ ';
    font-size: 0.7rem;
    color: var(--color-muted-foreground);
}

details[open] .path-summary::before {
    content: '▼ ';
}

.path-content {
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    border-top: 1px solid var(--color-border);
}

.path-row {
    display: flex;
    gap: 0.5rem;
}

.path-input {
    flex: 1;
    height: 2.25rem;
    border: 1px solid var(--color-input);
    border-radius: var(--radius-md);
    background: transparent;
    padding: 0 0.75rem;
    font-size: 0.875rem;
    color: var(--color-foreground);
    outline: none;
}

.path-input:focus {
    box-shadow: 0 0 0 1px var(--color-ring);
}

.path-no-sources {
    font-size: 0.85rem;
    color: var(--color-muted-foreground);
    margin: 0;
}

/* ---- Source cards (path section) ---- */

.detected-sources {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.source-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    background: var(--color-card);
}

.source-card-selectable {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    cursor: pointer;
    transition: border-color 0.15s;
}

.source-card-selectable:hover {
    border-color: var(--color-primary);
}

.source-card-checked {
    border-color: var(--color-primary);
    background: color-mix(in srgb, var(--color-primary) 6%, var(--color-card));
}

.source-checkbox {
    margin-top: 0.15rem;
    flex-shrink: 0;
    accent-color: var(--color-primary);
}

.source-card-body {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
}

.source-parser {
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--color-foreground);
}

.source-meta {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
    margin-top: 0.15rem;
}

.source-path {
    font-family: monospace;
    font-size: 0.75rem;
}
</style>
