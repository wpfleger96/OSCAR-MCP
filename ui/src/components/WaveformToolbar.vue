<template>
    <div class="waveform-toolbar">
        <Select
            :model-value="modelValue"
            :options="typeOptions"
            option-label="label"
            option-value="value"
            placeholder="Waveform type"
            size="small"
            @update:model-value="(v: string) => $emit('update:modelValue', v)"
        />
        <div class="toolbar-right">
            <Button
                v-if="multiWaveform"
                icon="pi pi-plus"
                label="Add Chart"
                size="small"
                severity="secondary"
                :disabled="chartCount >= 4"
                @click="$emit('add-chart')"
            />
            <ToggleButton
                :model-value="multiWaveform"
                on-label="Multi"
                off-label="Single"
                on-icon="pi pi-th-large"
                off-icon="pi pi-stop"
                size="small"
                @update:model-value="(v: boolean) => $emit('update:multiWaveform', v)"
            />
            <Button
                icon="pi pi-search-minus"
                label="Reset Zoom"
                size="small"
                severity="secondary"
                @click="$emit('reset-zoom')"
            />
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import Select from 'primevue/select'
import Button from 'primevue/button'
import ToggleButton from 'primevue/togglebutton'
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
