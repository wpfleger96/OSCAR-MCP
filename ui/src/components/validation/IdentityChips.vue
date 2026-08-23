<template>
    <div class="space-y-2">
        <div>
            <p class="mb-1 text-xs uppercase tracking-wide text-muted-foreground">Engine</p>
            <div class="flex flex-wrap gap-1">
                <Badge
                    v-for="entry in engineEntries"
                    :key="entry.key"
                    :variant="entry.differs ? 'default' : 'secondary'"
                >
                    {{ entry.key }} {{ entry.value }}
                </Badge>
                <span v-if="engineEntries.length === 0" class="text-xs text-muted-foreground"
                    >—</span
                >
            </div>
        </div>
        <div>
            <p class="mb-1 text-xs uppercase tracking-wide text-muted-foreground">Params</p>
            <div class="flex flex-wrap gap-1">
                <Badge
                    v-for="entry in paramEntries"
                    :key="entry.key"
                    :variant="entry.differs ? 'default' : 'outline'"
                >
                    {{ entry.key }} {{ entry.value }}
                </Badge>
                <span v-if="paramEntries.length === 0" class="text-xs text-muted-foreground"
                    >—</span
                >
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'

const props = defineProps<{
    identity?: Record<string, unknown> | null
    params?: Record<string, unknown> | null
    diffKeys: Set<string>
    paramDiffKeys: Set<string>
}>()

interface Entry {
    key: string
    value: string
    differs: boolean
}

function toEntries(obj: Record<string, unknown> | null | undefined, diff: Set<string>): Entry[] {
    if (!obj) return []
    return Object.keys(obj)
        .sort()
        .map((key) => ({ key, value: String(obj[key]), differs: diff.has(key) }))
}

const engineEntries = computed(() => toEntries(props.identity, props.diffKeys))
const paramEntries = computed(() => toEntries(props.params, props.paramDiffKeys))
</script>
