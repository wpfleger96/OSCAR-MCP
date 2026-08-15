<template>
    <Popover>
        <PopoverTrigger as-child>
            <button
                type="button"
                :aria-label="`Show details for Class ${flClass.classNum}: ${flClass.name}`"
                class="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
                <FlowClassGlyph :class-num="flClass.classNum" size="sm" />
            </button>
        </PopoverTrigger>
        <PopoverContent class="w-80" side="right">
            <div class="flex justify-center mb-3 py-2 bg-muted/30 rounded-md">
                <FlowClassGlyph :class-num="flClass.classNum" size="lg" />
            </div>
            <PopoverHeader>
                <PopoverTitle>Class {{ flClass.classNum }}: {{ flClass.name }}</PopoverTitle>
            </PopoverHeader>
            <SeverityBadge :severity="flClass.severity" />
            <p class="text-sm text-muted-foreground">
                {{ FLOW_LIMITATION_CLASSES[flClass.classNum]?.description }}
            </p>
            <p class="text-xs text-muted-foreground">
                <strong class="text-foreground">Visual:</strong>
                {{ FLOW_LIMITATION_CLASSES[flClass.classNum]?.visualCharacteristics }}
            </p>
            <p class="text-xs text-muted-foreground">
                <strong class="text-foreground">Clinical:</strong>
                {{ FLOW_LIMITATION_CLASSES[flClass.classNum]?.clinicalSignificance }}
            </p>
        </PopoverContent>
    </Popover>
</template>

<script setup lang="ts">
import {
    Popover,
    PopoverContent,
    PopoverHeader,
    PopoverTitle,
    PopoverTrigger,
} from '@/components/ui/popover'
import FlowClassGlyph from '@/components/FlowClassGlyph.vue'
import SeverityBadge from '@/components/SeverityBadge.vue'
import { FLOW_LIMITATION_CLASSES } from '@/utils/flowLimitation'

defineProps<{
    flClass: {
        classNum: number
        name: string
        severity: string
    }
}>()
</script>
