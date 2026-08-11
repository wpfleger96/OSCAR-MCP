<template>
    <div class="overflow-x-auto">
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead class="whitespace-nowrap">Start Date</TableHead>
                    <TableHead class="whitespace-nowrap">Brand</TableHead>
                    <TableHead class="whitespace-nowrap">Model</TableHead>
                    <TableHead class="whitespace-nowrap">Style</TableHead>
                    <TableHead class="whitespace-nowrap">Size</TableHead>
                    <TableHead>Notes</TableHead>
                    <TableHead v-if="canWrite"></TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                <TableRow v-for="entry in entries" :key="entry.id">
                    <TableCell class="whitespace-nowrap">{{
                        entry.start_date ? formatDateFull(entry.start_date) : '—'
                    }}</TableCell>
                    <TableCell class="whitespace-nowrap">{{ entry.brand ?? '—' }}</TableCell>
                    <TableCell class="whitespace-nowrap">{{ entry.model ?? '—' }}</TableCell>
                    <TableCell class="whitespace-nowrap">{{
                        entry.style ? styleLabel(entry.style) : '—'
                    }}</TableCell>
                    <TableCell class="whitespace-nowrap">{{ entry.size ?? '—' }}</TableCell>
                    <TableCell>{{ entry.notes ?? '—' }}</TableCell>
                    <TableCell v-if="canWrite" class="whitespace-nowrap text-right">
                        <Button
                            variant="ghost"
                            size="sm"
                            :disabled="saving"
                            @click="emit('edit', entry)"
                        >
                            Edit
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            :disabled="saving"
                            @click="emit('delete', entry)"
                        >
                            Delete
                        </Button>
                    </TableCell>
                </TableRow>
            </TableBody>
        </Table>
    </div>
</template>

<script setup lang="ts">
import { Button } from '@/components/ui/button'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { formatDateFull } from '@/utils/formatting'
import type { MaskLogEntryResponse } from '@/types'

defineProps<{
    entries: MaskLogEntryResponse[]
    canWrite: boolean
    saving: boolean
}>()

const emit = defineEmits<{
    edit: [entry: MaskLogEntryResponse]
    delete: [entry: MaskLogEntryResponse]
}>()

const STYLE_LABELS: Record<string, string> = {
    nasal: 'Nasal',
    full_face: 'Full Face',
    pillows: 'Pillows',
}

function styleLabel(style: string): string {
    return STYLE_LABELS[style] ?? style
}
</script>
