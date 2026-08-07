<template>
    <div v-if="jobs.length > 0" class="jobs-banner">
        <h2 class="jobs-banner-title">Import Jobs</h2>
        <div class="jobs-list">
            <div v-for="job in jobs" :key="job.job_id" class="job-row">
                <div class="job-icon">
                    <Loader2
                        v-if="isSpinning(job.stage)"
                        class="h-4 w-4 animate-spin text-muted-foreground"
                    />
                    <Clock
                        v-else-if="job.stage === 'queued' || job.stage === 'analysis_queued'"
                        class="h-4 w-4 text-muted-foreground"
                    />
                    <span v-else-if="job.stage === 'done'" class="job-state-icon job-state-success"
                        >&#x2713;</span
                    >
                    <span
                        v-else-if="job.stage === 'failed' || job.stage === 'analysis_failed'"
                        class="job-state-icon job-state-failed"
                        >&#x2717;</span
                    >
                    <span
                        v-else-if="job.stage === 'cancelled' || job.stage === 'analysis_cancelled'"
                        class="job-state-icon"
                        >&#x2014;</span
                    >
                    <AlertTriangle
                        v-else-if="job.stage === 'analysis_skipped'"
                        class="h-4 w-4 job-state-warning"
                    />
                    <span v-else class="job-state-icon">?</span>
                </div>
                <div class="job-info">
                    <div class="job-main-row">
                        <span class="job-label">{{ jobLabel(job) }}</span>
                        <span class="job-stage">{{ stageText(job.stage) }}</span>
                        <span
                            v-if="detailText(job)"
                            :class="['job-progress', detailClass(job.stage)]"
                            >{{ detailText(job) }}</span
                        >
                    </div>
                    <div class="job-timestamp">
                        <span v-if="job.finished_at">{{ formatTimestamp(job.finished_at) }}</span>
                        <span v-else>{{ formatTimestamp(job.created_at) }}</span>
                    </div>
                    <div
                        v-if="job.stage === 'done' && job.import_result"
                        class="job-result-summary"
                    >
                        <span class="count-imported"
                            >{{ job.import_result.total_imported }} imported</span
                        ><span class="count-sep">, </span
                        ><span class="count-skipped"
                            >{{ job.import_result.total_skipped }} skipped</span
                        ><span class="count-sep">, </span
                        ><span class="count-failed"
                            >{{ job.import_result.total_failed }} failed</span
                        ><template v-if="job.import_result.warnings.length > 0"
                            ><span class="count-sep"> · </span
                            ><span class="count-warnings"
                                >{{ job.import_result.warnings.length }} warning(s)</span
                            ></template
                        >
                    </div>
                </div>
                <Button
                    v-if="isActive(job.stage)"
                    variant="ghost"
                    size="icon"
                    title="Cancel job"
                    @click="emit('cancel', job.job_id)"
                >
                    <X class="h-4 w-4" />
                </Button>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { Loader2, Clock, X, AlertTriangle } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import type { PipelineJobStatus } from '@/types'
import { ACTIVE_PIPELINE_STAGES } from '@/api/importJobs'

defineProps<{ jobs: PipelineJobStatus[] }>()
const emit = defineEmits<{ cancel: [jobId: string] }>()

function isActive(stage: string): boolean {
    return ACTIVE_PIPELINE_STAGES.has(stage)
}

function isSpinning(stage: string): boolean {
    return stage === 'uploading' || stage === 'importing' || stage === 'analyzing'
}

function jobLabel(job: PipelineJobStatus): string {
    const unit = job.job_type === 'path' ? 'source(s)' : 'file(s)'
    return `${job.file_count} ${unit}`
}

const STAGE_LABELS: Record<string, string> = {
    uploading: 'Uploading',
    queued: 'Queued',
    importing: 'Importing',
    analysis_queued: 'Analysis queued',
    analyzing: 'Analyzing',
    done: 'Done',
    failed: 'Import failed',
    cancelled: 'Cancelled',
    analysis_failed: 'Analysis failed',
    analysis_cancelled: 'Analysis cancelled',
    analysis_skipped: 'Analysis skipped (queue full)',
}

function stageText(stage: string): string {
    return STAGE_LABELS[stage] ?? stage
}

function detailText(job: PipelineJobStatus): string | null {
    if (job.stage === 'importing') return job.progress_message ?? null
    if (job.stage === 'analyzing') {
        const la = job.linked_analysis
        if (la && la.progress_total > 0) {
            return `${la.progress_completed}/${la.progress_total} sessions`
        }
        if (job.sessions_imported != null) {
            return `${job.sessions_imported} session(s) imported`
        }
        return null
    }
    if (job.stage === 'analysis_queued' && job.sessions_imported != null) {
        return `${job.sessions_imported} session(s) imported`
    }
    if (job.stage === 'failed') return job.error_message ?? null
    if (job.stage === 'analysis_failed') return job.linked_analysis?.error_message ?? null
    if (job.stage === 'done' && job.sessions_imported != null) {
        return `${job.sessions_imported} session(s) imported`
    }
    return null
}

function detailClass(stage: string): string {
    return stage === 'failed' || stage === 'analysis_failed' ? 'job-error' : ''
}

function formatTimestamp(iso: string): string {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return '—'
    const diffMs = Date.now() - d.getTime()
    const diffMin = Math.floor(diffMs / 60_000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffH = Math.floor(diffMin / 60)
    if (diffH < 24) return `${diffH}h ago`
    return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}
</script>

<style scoped>
.job-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    background: var(--color-background, transparent);
}

.job-icon {
    padding-top: 0.1rem;
}

.job-state-warning {
    color: var(--color-warning);
}

.job-info {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    min-width: 0;
}

.job-main-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
}

.job-stage {
    font-size: 0.8rem;
    color: var(--color-muted-foreground);
}

.job-timestamp {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
}

.job-result-summary {
    font-size: 0.8rem;
}

.count-imported {
    color: var(--color-success);
}

.count-skipped {
    color: var(--color-muted-foreground);
}

.count-failed {
    color: var(--color-destructive);
}

.count-sep {
    color: var(--color-muted-foreground);
}

.count-warnings {
    color: color-mix(in srgb, var(--color-warning) 80%, var(--color-foreground));
}
</style>
