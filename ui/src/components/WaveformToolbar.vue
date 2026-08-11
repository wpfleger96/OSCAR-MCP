<template>
    <div class="waveform-toolbar">
        <div class="flex items-center gap-2">
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
            <InfoHint v-if="glossaryKey" :glossary-key="glossaryKey" />
        </div>
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
                :model-value="multiWaveform"
                variant="outline"
                size="sm"
                @update:model-value="(v: boolean) => $emit('update:multiWaveform', v)"
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
import InfoHint from '@/components/InfoHint.vue'
import { WAVEFORM_LABELS, WAVEFORM_GLOSSARY_MAP } from '@/types'
import type { WaveformType } from '@/types'

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

const glossaryKey = computed(() => WAVEFORM_GLOSSARY_MAP[props.modelValue as WaveformType] ?? null)
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
