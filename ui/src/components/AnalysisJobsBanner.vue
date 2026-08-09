<template>
    <div class="jobs-banner">
        <h2 class="jobs-banner-title">Analysis Jobs</h2>
        <div class="jobs-list">
            <div v-for="job in jobs" :key="job.job_id" class="job-row">
                <div class="job-icon">
                    <Loader2
                        v-if="job.state === 'running'"
                        class="h-4 w-4 animate-spin text-muted-foreground"
                    />
                    <Clock
                        v-else-if="job.state === 'queued'"
                        class="h-4 w-4 text-muted-foreground"
                    />
                    <span
                        v-else-if="job.state === 'succeeded'"
                        class="job-state-icon job-state-success"
                        >&#x2713;</span
                    >
                    <span v-else-if="job.state === 'failed'" class="job-state-icon job-state-failed"
                        >&#x2717;</span
                    >
                    <span v-else-if="job.state === 'cancelled'" class="job-state-icon"
                        >&#x2014;</span
                    >
                </div>
                <div class="job-info">
                    <span class="job-label">{{ jobLabel(job) }}</span>
                    <span
                        v-if="job.state === 'running' && job.progress_total > 0"
                        class="job-progress"
                    >
                        {{ job.progress_completed }}/{{ job.progress_total }} sessions
                    </span>
                    <span v-else-if="job.state === 'queued'" class="job-progress">Queued</span>
                    <span v-else-if="job.state === 'failed'" class="job-progress job-error">{{
                        job.error_message
                    }}</span>
                </div>
                <Button
                    v-if="job.state === 'queued' || job.state === 'running'"
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
import { Loader2, Clock, X } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import type { AnalysisJobInfo } from '@/api/analysis'

defineProps<{ jobs: AnalysisJobInfo[] }>()
const emit = defineEmits<{ cancel: [jobId: string] }>()

function jobLabel(job: AnalysisJobInfo): string {
    if (job.source === 'import') return `Post-import: ${job.session_count} session(s)`
    if (job.source === 'batch') return `Batch: ${job.session_count} session(s)`
    return `${job.session_count} session(s)`
}
</script>

<style scoped>
.job-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    background: var(--color-background, transparent);
}

.job-info {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    min-width: 0;
}
</style>
