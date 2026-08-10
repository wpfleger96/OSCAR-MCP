<template>
    <Popover>
        <PopoverTrigger as-child>
            <button
                type="button"
                class="inline-flex items-center justify-center align-middle text-muted-foreground hover:text-foreground transition-colors rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring h-4 w-4 shrink-0"
                :aria-label="`More information about ${resolvedLabel}`"
            >
                <Info class="h-3.5 w-3.5" />
            </button>
        </PopoverTrigger>
        <PopoverContent class="w-72">
            <PopoverHeader>
                <PopoverTitle>{{ resolvedLabel }}</PopoverTitle>
            </PopoverHeader>
            <slot v-if="$slots.default" />
            <template v-else>
                <PopoverDescription v-if="resolvedShort">{{ resolvedShort }}</PopoverDescription>
                <p v-if="resolvedLong" class="text-xs text-muted-foreground">{{ resolvedLong }}</p>
            </template>
        </PopoverContent>
    </Popover>
</template>

<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import { Info } from '@lucide/vue'
import { GLOSSARY } from '@/utils/glossary'
import {
    Popover,
    PopoverContent,
    PopoverDescription,
    PopoverHeader,
    PopoverTitle,
    PopoverTrigger,
} from '@/components/ui/popover'

const props = defineProps<{
    glossaryKey?: string
    label?: string
    short?: string
    long?: string
}>()

const entry = computed(() => (props.glossaryKey ? (GLOSSARY[props.glossaryKey] ?? null) : null))

const resolvedLabel = computed(() => props.label ?? entry.value?.label ?? '')
const resolvedShort = computed(() => props.short ?? entry.value?.short ?? '')
const resolvedLong = computed(() => props.long ?? entry.value?.long ?? '')

if (import.meta.env.DEV) {
    watchEffect(() => {
        if (props.glossaryKey && !entry.value && !props.label) {
            console.warn(`[InfoHint] No glossary entry found for key: "${props.glossaryKey}"`)
        }
    })
}
</script>
