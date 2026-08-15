<template>
    <div class="rcd-root">
        <ul class="rcd-list">
            <li v-for="code in codes" :key="code" class="rcd-code">{{ code }}</li>
        </ul>
        <div class="rcd-actions">
            <button
                type="button"
                class="rcd-btn"
                :class="{ 'rcd-btn--copied': copied }"
                @click="copyAll"
            >
                {{ copied ? 'Copied!' : 'Copy all' }}
            </button>
            <button type="button" class="rcd-btn" @click="download">Download .txt</button>
        </div>
        <p v-if="copyError" class="rcd-copy-error" role="alert">{{ copyError }}</p>
        <label class="rcd-ack-label">
            <input v-model="acknowledged" type="checkbox" class="rcd-ack-checkbox" />
            I've saved these recovery codes
        </label>
    </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ codes: string[] }>()
const acknowledged = defineModel<boolean>({ default: false })

const copied = ref(false)
const copyError = ref<string | null>(null)

async function copyAll() {
    try {
        await navigator.clipboard.writeText(props.codes.join('\n'))
        copied.value = true
        setTimeout(() => {
            copied.value = false
        }, 2000)
    } catch {
        copyError.value = 'Copy failed — select the text manually'
        setTimeout(() => {
            copyError.value = null
        }, 3000)
    }
}

function download() {
    const text = props.codes.join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'snore-recovery-codes.txt'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
}
</script>

<style scoped>
.rcd-root {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.rcd-list {
    list-style: none;
    padding: 0.75rem;
    margin: 0;
    background: var(--color-accent);
    border-radius: 6px;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.25rem 1.5rem;
}

.rcd-code {
    font-family: monospace;
    font-size: 0.9rem;
    letter-spacing: 0.05em;
    color: var(--color-foreground);
}

.rcd-actions {
    display: flex;
    gap: 0.5rem;
}

.rcd-btn {
    background: none;
    border: 1px solid var(--color-border);
    border-radius: 4px;
    padding: 0.125rem 0.5rem;
    font-size: 0.8rem;
    cursor: pointer;
    color: var(--color-foreground);
    white-space: nowrap;
}

.rcd-btn--copied {
    color: var(--color-success);
    border-color: var(--color-success);
}

.rcd-btn:hover {
    background: var(--color-accent);
}

.rcd-copy-error {
    font-size: 0.8rem;
    color: var(--color-destructive);
    margin: 0;
}

.rcd-ack-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.875rem;
    cursor: pointer;
}

.rcd-ack-checkbox {
    cursor: pointer;
}
</style>
