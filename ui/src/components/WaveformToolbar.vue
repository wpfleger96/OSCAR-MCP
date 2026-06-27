<template>
    <div class="waveform-toolbar">
        <Select
            :model-value="modelValue"
            @update:model-value="(v) => $emit('update:modelValue', v as string)"
        >
            <SelectTrigger class="w-[180px] h-8 text-sm">
                <SelectValue placeholder="Waveform type" />
            </SelectTrigger>
            <SelectContent>
                <SelectItem v-for="opt in typeOptions" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                </SelectItem>
            </SelectContent>
        </Select>
        <div class="toolbar-right">
            <Button
                v-if="multiWaveform"
                variant="outline"
                size="sm"
                :disabled="chartCount >= 4"
                @click="$emit('add-chart')"
            >
                <Plus class="mr-2 h-4 w-4" />
                Add Chart
            </Button>
            <Toggle
                :pressed="multiWaveform"
                variant="outline"
                size="sm"
                @update:pressed="(v: boolean) => $emit('update:multiWaveform', v)"
            >
                <LayoutGrid v-if="multiWaveform" class="mr-2 h-4 w-4" />
                <Square v-else class="mr-2 h-4 w-4" />
                {{ multiWaveform ? 'Multi' : 'Single' }}
            </Toggle>
            <Button variant="outline" size="sm" @click="$emit('reset-zoom')">
                <ZoomOut class="mr-2 h-4 w-4" />
                Reset Zoom
            </Button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Plus, ZoomOut, LayoutGrid, Square } from '@lucide/vue'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Toggle } from '@/components/ui/toggle'
import { WAVEFORM_LABELS } from '@/types'

const props = defineProps<{
    availableTypes: string[]
    modelValue: string
    multiWaveform: boolean
    chartCount: number
}>()

defineEmits<{
    'update:modelValue': [value: string]
    'update:multiWaveform': [value: boolean]
    'reset-zoom': []
    'add-chart': []
}>()

const typeOptions = computed(() =>
    props.availableTypes.map((t) => ({
        value: t,
        label: WAVEFORM_LABELS[t] ?? t,
    })),
)
</script>

<style scoped>
.waveform-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.5rem 0;
    margin-bottom: 0.5rem;
}

.toolbar-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
</style>
