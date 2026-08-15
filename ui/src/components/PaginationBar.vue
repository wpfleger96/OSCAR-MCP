<template>
    <div v-if="total > pageSize" class="pagination-bar flex items-center justify-between px-2 py-4">
        <span class="text-sm text-muted-foreground">
            {{ offset + 1 }}–{{ Math.min(offset + pageSize, total) }} of {{ total }}
        </span>
        <div class="pagination-controls flex gap-2">
            <Button
                variant="outline"
                size="sm"
                :disabled="offset === 0"
                @click="$emit('page', offset - pageSize)"
            >
                Previous
            </Button>
            <Button
                variant="outline"
                size="sm"
                :disabled="offset + pageSize >= total"
                @click="$emit('page', offset + pageSize)"
            >
                Next
            </Button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { Button } from '@/components/ui/button'

defineProps<{
    offset: number
    pageSize: number
    total: number
}>()

defineEmits<{
    page: [newOffset: number]
}>()
</script>

<style scoped>
/* Mobile: wrap the count onto its own line and stretch the buttons into
   full-width tap targets. Desktop layout is untouched. */
@media (max-width: 767.98px) {
    .pagination-bar {
        flex-wrap: wrap;
        row-gap: 0.75rem;
    }

    .pagination-controls {
        flex-basis: 100%;
    }

    .pagination-controls > * {
        flex: 1;
        min-height: var(--tap-target);
    }
}
</style>
