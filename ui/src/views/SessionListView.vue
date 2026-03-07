<template>
    <div class="session-list">
        <h1 class="page-title">Sessions</h1>
        <DataTable
            :value="sessions"
            :loading="loading"
            :lazy="true"
            :rows="pageSize"
            :total-records="totalRecords"
            :first="offset"
            paginator
            striped-rows
            selection-mode="single"
            class="sessions-table"
            @row-click="navigateToSession"
            @page="onPage"
        >
            <template #empty>
                <div class="table-empty">No sessions found.</div>
            </template>
            <Column field="start_time" header="Date" style="min-width: 180px">
                <template #body="{ data }: { data: SessionListItem }">
                    {{ formatDate(data.start_time) }}
                </template>
            </Column>
            <Column field="duration_hours" header="Duration" style="width: 110px">
                <template #body="{ data }: { data: SessionListItem }">
                    {{ data.duration_hours.toFixed(1) }}h
                </template>
            </Column>
            <Column field="ahi" header="AHI" style="width: 90px">
                <template #body="{ data }: { data: SessionListItem }">
                    <span :class="ahiClass(data.ahi)">{{ data.ahi?.toFixed(1) ?? '---' }}</span>
                </template>
            </Column>
            <Column header="Device">
                <template #body="{ data }: { data: SessionListItem }">
                    {{ data.manufacturer }} {{ data.model }}
                </template>
            </Column>
            <Column field="enabled" header="Status" style="width: 90px">
                <template #body="{ data }: { data: SessionListItem }">
                    <Tag
                        :value="data.enabled ? 'Active' : 'Disabled'"
                        :severity="data.enabled ? 'success' : 'secondary'"
                    />
                </template>
            </Column>
        </DataTable>
        <div v-if="error" class="error-msg">{{ error }}</div>
    </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import { getSessions } from '@/api/sessions'
import type { SessionListItem } from '@/types'

const router = useRouter()
const sessions = ref<SessionListItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const totalRecords = ref(0)
const offset = ref(0)
const pageSize = 25

function formatDate(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

function ahiClass(ahi: number | null): string {
    if (ahi == null) return ''
    if (ahi < 5) return 'ahi-good'
    if (ahi < 15) return 'ahi-mild'
    return 'ahi-severe'
}

function navigateToSession(event: { data: SessionListItem }): void {
    void router.push({ name: 'session-detail', params: { id: event.data.id } })
}

async function fetchPage(newOffset: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
        const result = await getSessions({
            limit: pageSize,
            offset: newOffset,
            sort_by: 'date-desc',
        })
        sessions.value = result.items
        totalRecords.value = result.total
        offset.value = newOffset
    } catch (err: unknown) {
        error.value = err instanceof Error ? err.message : 'Failed to load sessions'
    } finally {
        loading.value = false
    }
}

function onPage(event: { first: number }): void {
    void fetchPage(event.first)
}

onMounted(() => void fetchPage(0))
</script>

<style scoped>
.session-list {
    max-width: 1100px;
}

.page-title {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 1.25rem;
}

.sessions-table {
    cursor: pointer;
}

.table-empty {
    padding: 2rem;
    text-align: center;
    color: var(--p-text-muted-color, #6b7280);
}

.error-msg {
    margin-top: 1rem;
    color: var(--p-red-500, #ef4444);
}

.ahi-good {
    color: #16a34a;
    font-weight: 600;
}
.ahi-mild {
    color: #ca8a04;
    font-weight: 600;
}
.ahi-severe {
    color: #dc2626;
    font-weight: 600;
}
</style>
