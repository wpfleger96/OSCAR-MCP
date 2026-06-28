<template>
    <div class="import-view">
        <h1 class="page-title">Import Data</h1>

        <!-- Step indicator -->
        <div class="step-indicator">
            <div
                v-for="n in 3"
                :key="n"
                class="step-dot"
                :class="{ active: step === n, done: step > n }"
            >
                <span class="step-num">{{ n }}</span>
                <span class="step-label">{{ stepLabels[n - 1] }}</span>
            </div>
        </div>

        <!-- Step 1: Source Selection -->
        <div v-if="step === 1" class="step-content">
            <h2 class="step-title">Select Source</h2>

            <div v-if="isLocalhost" class="source-section">
                <h3 class="section-heading">Filesystem Path</h3>
                <div class="path-row">
                    <input
                        v-model="sourcePath"
                        type="text"
                        placeholder="/mnt/sd-card"
                        class="path-input"
                        @keydown.enter="handleDetect"
                    />
                    <Button :disabled="!sourcePath || detecting" @click="handleDetect">
                        <Loader2 v-if="detecting" class="mr-2 h-4 w-4 animate-spin" />
                        Detect Sources
                    </Button>
                </div>
                <p v-if="detectError" class="error-text">{{ detectError }}</p>

                <div v-if="detectedSources.length > 0" class="detected-sources">
                    <div v-for="(src, i) in detectedSources" :key="i" class="source-card">
                        <div class="source-parser">{{ src.parser_name }}</div>
                        <div v-if="src.device_serial" class="source-meta">
                            Serial: {{ src.device_serial }}
                        </div>
                        <div v-if="src.data_root" class="source-meta">
                            Data root: {{ src.data_root }}
                        </div>
                        <div class="source-meta source-path">{{ src.root_path }}</div>
                    </div>
                </div>
            </div>

            <div class="source-section">
                <h3 class="section-heading">File Upload</h3>
                <input
                    ref="fileInputRef"
                    type="file"
                    webkitdirectory
                    multiple
                    accept=".edf,.xml,.csv,.dat,.crc,.gz"
                    class="hidden"
                    @change="onFileChange"
                />
                <div
                    class="drop-zone"
                    :class="{ dragging: isDragging }"
                    @click="fileInputRef?.click()"
                    @dragover.prevent="isDragging = true"
                    @dragleave.prevent="isDragging = false"
                    @drop.prevent="onDrop"
                >
                    <Upload class="drop-icon" />
                    <p v-if="!selectedFiles" class="drop-text">
                        Drop SD card folder here or click to browse
                    </p>
                    <p v-else class="drop-text">{{ selectedFiles.length }} files selected</p>
                    <p class="drop-warning">
                        For best results, use the folder picker. Drag-and-drop may not preserve
                        directory structure.
                    </p>
                </div>
            </div>

            <div class="step-actions">
                <Button :disabled="!canProceed" @click="handleImport">
                    <Upload class="mr-2 h-4 w-4" />
                    Import
                </Button>
            </div>
        </div>

        <!-- Step 2: Progress -->
        <div v-if="step === 2" class="step-content">
            <h2 class="step-title">Importing…</h2>

            <div v-if="selectedFiles && uploadProgress < 100" class="progress-section">
                <p class="progress-label">Uploading files… {{ uploadProgress }}%</p>
                <div class="progress-track">
                    <div class="progress-fill" :style="{ width: uploadProgress + '%' }" />
                </div>
            </div>

            <div v-if="importing" class="processing-row">
                <Loader2 class="h-6 w-6 animate-spin text-muted-foreground" />
                <span class="processing-text">Processing…</span>
            </div>

            <p v-if="importError" class="error-text">{{ importError }}</p>

            <div v-if="importError" class="step-actions">
                <Button variant="outline" @click="step = 1">
                    <ArrowLeft class="mr-2 h-4 w-4" />
                    Back
                </Button>
            </div>
        </div>

        <!-- Step 3: Results -->
        <div v-if="step === 3 && importResult" class="step-content">
            <h2 class="step-title">Import Complete</h2>

            <div class="stats-grid">
                <StatCard label="Imported" :value="importResult.total_imported" />
                <StatCard label="Skipped" :value="importResult.total_skipped" />
                <StatCard label="Failed" :value="importResult.total_failed" />
            </div>

            <div
                v-if="importResult.warnings && importResult.warnings.length > 0"
                class="warnings-box"
            >
                <div class="warnings-header">
                    <AlertTriangle class="h-4 w-4" />
                    Warnings
                </div>
                <ul class="warnings-list">
                    <li v-for="(w, i) in importResult.warnings" :key="i">{{ w }}</li>
                </ul>
            </div>

            <div
                v-if="importResult.sources && importResult.sources.length > 0"
                class="sources-results"
            >
                <h3 class="section-heading">Per-source breakdown</h3>
                <div v-for="(sr, i) in importResult.sources" :key="i" class="source-result-card">
                    <div class="source-result-header">
                        <span class="source-parser">{{ sr.source.parser_name }}</span>
                        <span v-if="sr.source.device_serial" class="source-meta">
                            {{ sr.source.device_serial }}
                        </span>
                    </div>
                    <div class="source-result-counts">
                        <span class="count-item count-imported">{{ sr.imported }} imported</span>
                        <span class="count-item count-skipped">{{ sr.skipped }} skipped</span>
                        <span class="count-item count-failed">{{ sr.failed }} failed</span>
                    </div>
                    <div v-if="sr.warnings && sr.warnings.length > 0" class="source-warnings">
                        <div v-for="(w, j) in sr.warnings" :key="j" class="source-warning-item">
                            <AlertTriangle class="h-3 w-3" />
                            {{ w }}
                        </div>
                    </div>
                </div>
            </div>

            <div class="step-actions">
                <Button variant="outline" @click="resetState">Import More</Button>
                <Button @click="router.push({ name: 'sessions' })">
                    <Check class="mr-2 h-4 w-4" />
                    View Sessions
                </Button>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import type { AxiosProgressEvent } from 'axios'
import type { ImportSource, ImportResult } from '@/types'
import { detectSources, importFiles } from '@/api/import'
import { Button } from '@/components/ui/button'
import StatCard from '@/components/StatCard.vue'
import { Loader2, Upload, Check, AlertTriangle, ArrowLeft } from '@lucide/vue'

const router = useRouter()

const stepLabels = ['Source', 'Import', 'Results']

const step = ref(1)
// UI-only gate — not a security boundary
const isLocalhost =
    window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'

// Step 1 state
const sourcePath = ref('')
const detectedSources = ref<ImportSource[]>([])
const detecting = ref(false)
const detectError = ref<string | null>(null)
const selectedFiles = ref<FileList | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragging = ref(false)

// Step 2 state
const importing = ref(false)
const uploadProgress = ref(0)
const importError = ref<string | null>(null)

// Step 3 state
const importResult = ref<ImportResult | null>(null)

const canProceed = computed(() => selectedFiles.value !== null)

async function handleDetect() {
    if (!sourcePath.value) return
    detecting.value = true
    detectError.value = null
    try {
        detectedSources.value = await detectSources({ path: sourcePath.value })
    } catch (e: unknown) {
        detectError.value = e instanceof Error ? e.message : 'Detection failed'
    } finally {
        detecting.value = false
    }
}

function onFileChange(event: Event) {
    const input = event.target as HTMLInputElement
    selectedFiles.value = input.files && input.files.length > 0 ? input.files : null
}

function onDrop(event: DragEvent) {
    isDragging.value = false
    const files = event.dataTransfer?.files
    selectedFiles.value = files && files.length > 0 ? files : null
}

async function handleImport() {
    step.value = 2
    importing.value = true
    importError.value = null
    uploadProgress.value = 0
    try {
        const onProgress = (event: AxiosProgressEvent) => {
            if (event.total) {
                uploadProgress.value = Math.round((event.loaded / event.total) * 100)
            }
        }
        importResult.value = await importFiles(selectedFiles.value!, onProgress)
        step.value = 3
    } catch (e: unknown) {
        importError.value = e instanceof Error ? e.message : 'Import failed'
    } finally {
        importing.value = false
    }
}

function resetState() {
    step.value = 1
    sourcePath.value = ''
    detectedSources.value = []
    detectError.value = null
    selectedFiles.value = null
    uploadProgress.value = 0
    importError.value = null
    importResult.value = null
    if (fileInputRef.value) fileInputRef.value.value = ''
}
</script>

<style scoped>
.import-view {
    max-width: 800px;
    margin: 0 auto;
    padding: 1.5rem;
}

.step-indicator {
    display: flex;
    gap: 0;
    margin-bottom: 2rem;
    border: 1px solid var(--color-border);
    border-radius: 8px;
    overflow: hidden;
}

.step-dot {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0.6rem 0.5rem;
    background: var(--color-card);
    border-right: 1px solid var(--color-border);
    gap: 0.2rem;
}

.step-dot:last-child {
    border-right: none;
}

.step-dot.active {
    background: var(--color-primary);
}

.step-dot.active .step-num,
.step-dot.active .step-label {
    color: var(--color-primary-foreground);
}

.step-dot.done {
    background: var(--color-muted);
}

.step-num {
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--color-muted-foreground);
}

.step-label {
    font-size: 0.7rem;
    color: var(--color-muted-foreground);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.step-content {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
}

.step-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--color-foreground);
}

.source-section {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.section-heading {
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted-foreground);
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
    background: var(--color-card);
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

.drop-warning {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
    text-align: center;
    font-style: italic;
}

.step-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
    padding-top: 0.5rem;
}

.progress-section {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.progress-label {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
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

.processing-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 1rem 0;
}

.processing-text {
    font-size: 0.875rem;
    color: var(--color-muted-foreground);
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
}

.warnings-box {
    border: 1px solid color-mix(in srgb, var(--color-warning, #f59e0b) 40%, transparent);
    background: color-mix(in srgb, var(--color-warning, #f59e0b) 10%, transparent);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.warnings-header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--color-destructive);
}

.warnings-list {
    margin: 0;
    padding-left: 1rem;
    font-size: 0.8rem;
    color: var(--color-destructive);
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
}

.sources-results {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.source-result-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    background: var(--color-card);
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
}

.source-result-header {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
}

.source-result-counts {
    display: flex;
    gap: 1rem;
    font-size: 0.8rem;
}

.count-item {
    font-weight: 500;
}

.count-imported {
    color: var(--color-success, #16a34a);
}

.count-skipped {
    color: var(--color-muted-foreground);
}

.count-failed {
    color: var(--color-destructive);
}

.source-warnings {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    margin-top: 0.2rem;
}

.source-warning-item {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.75rem;
    color: var(--color-destructive);
}

.error-text {
    font-size: 0.875rem;
    color: var(--color-destructive);
}
</style>
