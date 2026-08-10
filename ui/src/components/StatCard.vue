<template>
    <div class="stat-card">
        <div class="stat-label">
            {{ label
            }}<span v-if="hintKey" class="ml-1 normal-case"
                ><InfoHint :glossary-key="hintKey"
            /></span>
        </div>
        <div class="stat-value">
            <template v-if="value != null">
                {{ decimals != null ? value.toFixed(decimals) : value }}
                <span v-if="unit" class="stat-unit">{{ unit }}</span>
            </template>
            <template v-else>
                <span class="stat-empty">---</span>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
import InfoHint from '@/components/InfoHint.vue'

defineProps<{
    label: string
    value: number | null | undefined
    unit?: string
    decimals?: number
    hintKey?: string
}>()
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
