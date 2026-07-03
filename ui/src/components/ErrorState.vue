<template>
    <div class="error-state">
        <AlertTriangle class="h-5 w-5" />
        <span>{{ message }}</span>
        <Button v-if="retry" variant="outline" size="sm" @click="retry()">Retry</Button>
    </div>
</template>

<script setup lang="ts">
import { AlertTriangle } from '@lucide/vue'
import { Button } from '@/components/ui/button'

defineProps<{ message: string; retry?: () => void }>()
</script>
