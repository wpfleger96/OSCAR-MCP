<template>
    <div v-if="total > pageSize" class="flex items-center justify-between px-2 py-4">
        <span class="text-sm text-muted-foreground">
            {{ offset + 1 }}–{{ Math.min(offset + pageSize, total) }} of {{ total }}
        </span>
        <div class="flex gap-2">
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
