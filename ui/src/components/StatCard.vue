<template>
    <div class="stat-card">
        <div class="stat-label">
            {{ label
            }}<span v-if="glossaryKey" class="ml-1 normal-case"
                ><InfoHint :glossary-key="glossaryKey"
            /></span>
        </div>
        <div class="stat-value">
            <span v-if="value != null && display != null">{{ display }}</span>
            <template v-else-if="value != null">
                {{ decimals != null ? value.toFixed(decimals) : value }}
                <span v-if="unit" class="stat-unit">{{ unit }}</span>
            </template>
            <template v-else>
                <span class="stat-empty" :title="reasonLabel ?? undefined">---</span>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import InfoHint from '@/components/InfoHint.vue'
import { nullReasonLabel } from '@/utils/formatting'

const props = defineProps<{
    label: string
    value: number | null | undefined
    unit?: string
    decimals?: number
    // Pre-formatted display string that overrides numeric formatting when `value`
    // is present (e.g. an adaptive-precision percent). The em-dash empty state still
    // keys off `value`, so a null value keeps its reason tooltip.
    display?: string | null
    glossaryKey?: string
    // Null-with-reason code (e.g. 'analysis_not_run'); shown as a tooltip on the
    // em-dash state to explain why a value is absent for this night.
    reason?: string | null
}>()

const reasonLabel = computed(() => nullReasonLabel(props.reason))
</script>

<style scoped>
.stat-card {
    background: var(--color-card);
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
}

.stat-label {
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted-foreground);
    margin-bottom: 0.35rem;
}

.stat-value {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--color-foreground);
    line-height: 1.2;
}

.stat-unit {
    font-size: 0.85rem;
    font-weight: 400;
    color: var(--color-muted-foreground);
    margin-left: 0.2rem;
}

.stat-empty {
    color: var(--color-muted-foreground);
    font-size: 1.1rem;
}
</style>
