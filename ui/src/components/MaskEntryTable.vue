<template>
    <div v-if="isMobile" class="card-list">
        <div v-for="entry in entries" :key="entry.id" class="data-card">
            <div class="data-card-header">
                {{ entry.start_date ? formatDateFull(entry.start_date) : '—' }}
            </div>
            <div class="data-card-row">
                <span class="data-card-label">Brand</span>
                <span class="data-card-value">{{ entry.brand ?? '—' }}</span>
            </div>
            <div class="data-card-row">
                <span class="data-card-label">Model</span>
                <span class="data-card-value">{{ entry.model ?? '—' }}</span>
            </div>
            <div class="data-card-row">
                <span class="data-card-label">Style</span>
                <span class="data-card-value">{{
                    entry.style ? styleLabel(entry.style) : '—'
                }}</span>
            </div>
            <div class="data-card-row">
                <span class="data-card-label">Size</span>
                <span class="data-card-value">{{ entry.size ?? '—' }}</span>
            </div>
            <div class="data-card-row">
                <span class="data-card-label">Notes</span>
                <span class="data-card-value">{{ entry.notes ?? '—' }}</span>
            </div>
            <div v-if="canWrite" class="data-card-actions">
                <Button variant="ghost" size="sm" :disabled="saving" @click="emit('edit', entry)">
                    Edit
                </Button>
                <Button variant="ghost" size="sm" :disabled="saving" @click="emit('delete', entry)">
                    Delete
                </Button>
            </div>
        </div>
    </div>
    <div v-else class="overflow-x-auto">
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
import { useIsMobile } from '@/composables/useIsMobile'
import { formatDateFull } from '@/utils/formatting'
import { styleLabel } from '@/utils/maskOptions'
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

const { isMobile } = useIsMobile()
</script>
