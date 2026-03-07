<template>
    <div class="stat-card">
        <div class="stat-label">{{ label }}</div>
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
defineProps<{
    label: string
    value: number | null | undefined
    unit?: string
    decimals?: number
}>()
</script>

<style scoped>
.stat-card {
    background: var(--p-surface-card, #fff);
    border: 1px solid var(--p-surface-border, #e2e8f0);
    border-radius: 8px;
    padding: 1rem 1.25rem;
}

.stat-label {
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--p-text-muted-color, #6b7280);
    margin-bottom: 0.35rem;
}

.stat-value {
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--p-text-color, #1a1a1a);
    line-height: 1.2;
}

.stat-unit {
    font-size: 0.85rem;
    font-weight: 400;
    color: var(--p-text-muted-color, #6b7280);
    margin-left: 0.2rem;
}

.stat-empty {
    color: var(--p-text-muted-color, #6b7280);
    font-size: 1.1rem;
}
</style>
