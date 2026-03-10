<template>
    <Dialog
        :visible="visible"
        :header="title"
        modal
        :style="{ width: '450px' }"
        @update:visible="$emit('update:visible', $event)"
    >
        <div v-if="loading" class="dialog-loading">
            <i class="pi pi-spin pi-spinner" /> Loading preview...
        </div>
        <div v-else>
            <p class="dialog-message">{{ message }}</p>
            <slot name="preview" />
        </div>
        <template #footer>
            <Button label="Cancel" severity="secondary" @click="$emit('update:visible', false)" />
            <Button
                label="Delete"
                severity="danger"
                icon="pi pi-trash"
                :loading="deleting"
                :disabled="loading"
                @click="$emit('confirm')"
            />
        </template>
    </Dialog>
</template>

<script setup lang="ts">
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'

defineProps<{
    visible: boolean
    title: string
    message: string
    loading: boolean
    deleting: boolean
}>()

defineEmits<{
    'update:visible': [value: boolean]
    confirm: []
}>()
</script>

<style scoped>
.dialog-loading {
    padding: 2rem;
    text-align: center;
    color: var(--p-text-muted-color, #6b7280);
}

.dialog-message {
    margin-bottom: 1rem;
    color: var(--p-text-color, #1a1a1a);
}
</style>
