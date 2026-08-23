<template>
    <RouterLink :to="`/sessions/${sessionId}/analysis`" class="text-primary hover:underline">
        {{ formatDateShort(date) }}
    </RouterLink>
    <span
        v-if="skippedReason"
        class="ml-1 text-xs text-muted-foreground"
        :title="nullReasonLabel(skippedReason) ?? undefined"
        >({{ showReason ? skippedReason : 'skipped' }})</span
    >
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { formatDateShort, nullReasonLabel } from '@/utils/formatting'

// Session date link + optional skipped-reason tag, shared by the FL, breath-trends,
// and RERA session tables. `showReason` renders the raw reason code inline (RERA)
// versus a generic "(skipped)" tag (FL/breaths); the full sentence is always the title.
defineProps<{
    sessionId: number
    date: string
    skippedReason?: string | null
    showReason?: boolean
}>()
</script>
