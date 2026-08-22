<template>
    <div class="jobs-banner">
        <h2 class="jobs-banner-title">Validation Runs</h2>
        <div class="jobs-list">
            <div v-for="run in runs" :key="run.run_id" class="job-row">
                <div class="job-icon">
                    <Loader2
                        v-if="run.state === 'running'"
                        class="h-4 w-4 animate-spin text-muted-foreground"
                    />
                    <Clock
                        v-else-if="run.state === 'queued'"
                        class="h-4 w-4 text-muted-foreground"
                    />
                    <span
                        v-else-if="run.state === 'succeeded'"
                        class="job-state-icon job-state-success"
                        >&#x2713;</span
                    >
                    <span v-else-if="run.state === 'failed'" class="job-state-icon job-state-failed"
                        >&#x2717;</span
                    >
                    <span v-else-if="run.state === 'cancelled'" class="job-state-icon"
                        >&#x2014;</span
                    >
                </div>
                <div class="job-info">
                    <div class="job-main-row">
                        <span class="job-label">{{ runLabel(run) }}</span>
                        <span v-if="run.state === 'queued'" class="job-progress">Queued</span>
                        <span v-else-if="run.state === 'running'" class="job-progress"
                            >Running…</span
                        >
                        <span v-else-if="run.state === 'failed'" class="job-progress job-error">{{
                            run.error_message
                        }}</span>
                    </div>
                    <div v-if="run.finished_at || run.created_at" class="job-timestamp">
                        <span v-if="run.finished_at">{{
                            formatRelativeTime(run.finished_at)
                        }}</span>
                        <span v-else>{{ formatRelativeTime(run.created_at) }}</span>
                    </div>
                </div>
                <Button
                    v-if="run.state === 'queued' || run.state === 'running'"
                    variant="ghost"
                    size="icon"
                    title="Cancel run"
                    @click="emit('cancel', run.run_id)"
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
import type { ValidationRunStatus } from '@/types'
import { formatRelativeTime } from '@/utils/formatting'
import { VALIDATOR_LABELS } from '@/utils/validationMetrics'

defineProps<{ runs: ValidationRunStatus[] }>()
const emit = defineEmits<{ cancel: [runId: number] }>()

function runLabel(run: ValidationRunStatus): string {
    const type = VALIDATOR_LABELS[run.validator_type as keyof typeof VALIDATOR_LABELS]
    return `${type ?? run.validator_type}: ${run.date_from} → ${run.date_to}`
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

.job-timestamp {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
}
</style>
