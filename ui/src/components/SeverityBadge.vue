<template>
    <span class="text-xs px-1.5 py-0.5 rounded-full font-medium" :class="severityClass">{{
        severity
    }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ severity: string }>()

const severityClass = computed(() => {
    switch (props.severity) {
        case 'normal':
            return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
        case 'mild':
            return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
        case 'mild-moderate':
            return 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400'
        case 'moderate':
            return 'bg-orange-200 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300'
        case 'moderate-severe':
            return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
        case 'severe':
            return 'bg-red-200 text-red-800 dark:bg-red-900/40 dark:text-red-300'
        default:
            return 'bg-muted text-muted-foreground'
    }
})
</script>
