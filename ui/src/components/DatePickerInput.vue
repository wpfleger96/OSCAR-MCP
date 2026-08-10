<script lang="ts" setup>
import { ref, computed } from 'vue'
import type { DateValue } from 'reka-ui'
import { CalendarIcon } from '@lucide/vue'
import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { strToCalendarDate, calendarDateToStr } from '@/composables/useAvailableDates'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
    defineProps<{
        modelValue?: string
        placeholder?: string
        isDateDisabled?: (date: DateValue) => boolean
        minValue?: DateValue
        maxValue?: DateValue
        class?: string
    }>(),
    { placeholder: 'Pick a date' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const open = ref(false)

const calendarValue = computed(() =>
    props.modelValue ? strToCalendarDate(props.modelValue) : undefined,
)

const displayValue = computed(() => {
    if (!props.modelValue) return props.placeholder
    const [y, m, d] = props.modelValue.split('-')
    return `${m}/${d}/${y}`
})

function onSelect(val: DateValue | undefined) {
    if (!val) return
    emit('update:modelValue', calendarDateToStr(val))
    open.value = false
}
</script>

<template>
    <div :class="props.class">
        <Popover v-model:open="open">
            <PopoverTrigger as-child>
                <button
                    type="button"
                    class="date-picker-trigger"
                    :class="{ 'text-muted-foreground': !modelValue }"
                >
                    <CalendarIcon class="h-4 w-4 shrink-0 opacity-50" />
                    {{ displayValue }}
                </button>
            </PopoverTrigger>
            <PopoverContent class="w-auto p-0" align="start">
                <Calendar
                    :model-value="calendarValue"
                    layout="month-and-year"
                    :is-date-disabled="isDateDisabled"
                    :min-value="minValue"
                    :max-value="maxValue"
                    @update:model-value="onSelect"
                />
            </PopoverContent>
        </Popover>
    </div>
</template>

<style scoped>
.date-picker-trigger {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    height: 2.25rem;
    min-width: 8rem;
    border-radius: 0.375rem;
    border: 1px solid var(--color-input);
    background: transparent;
    padding: 0.25rem 0.75rem;
    font-size: 0.875rem;
    box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05);
    transition:
        color 0.15s,
        border-color 0.15s;
    color: var(--color-foreground);
    cursor: pointer;
    white-space: nowrap;
}
.date-picker-trigger:focus-visible {
    outline: none;
    box-shadow: 0 0 0 1px var(--color-ring);
}
</style>
