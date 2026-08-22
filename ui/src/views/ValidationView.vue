<template>
    <div class="mx-auto px-4 py-6" style="max-width: 1200px">
        <h1 class="mb-2 text-2xl font-bold">Validation</h1>
        <p class="mb-6 max-w-3xl text-sm text-muted-foreground">
            Validate SNORE's programmatic analysis against the device's own signals and an
            independent Apple Health axis. Event detection (apnea/hypopnea) is measured against
            machine-flagged events; the FL, RERA, and Apple metrics are
            <span class="font-medium">experimental trend instruments</span>, not clinically
            validated absolute measurements. Runs are persisted — use History to revisit a run and
            Compare Runs to measure the effect of an algorithm or parameter change.
        </p>

        <Tabs v-model="activeTab">
            <TabsList>
                <TabsTrigger v-for="t in TABS" :key="t" :value="t">
                    {{ VALIDATOR_LABELS[t] }}
                </TabsTrigger>
            </TabsList>

            <TabsContent value="events">
                <EventsValidationPanel :load-run-id="loadRunFor('events')" />
            </TabsContent>
            <TabsContent value="fl">
                <FlValidationPanel :load-run-id="loadRunFor('fl')" />
            </TabsContent>
            <TabsContent value="breaths">
                <BreathTrendsPanel :load-run-id="loadRunFor('breaths')" />
            </TabsContent>
            <TabsContent value="rera">
                <ReraValidationPanel :load-run-id="loadRunFor('rera')" />
            </TabsContent>
            <TabsContent value="apple">
                <AppleCrossPanel :load-run-id="loadRunFor('apple')" />
            </TabsContent>
        </Tabs>

        <Separator class="my-8" />
        <RunComparison />

        <Separator class="my-8" />
        <ValidationRunHistory @select="onSelectRun" />
    </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import EventsValidationPanel from '@/components/validation/EventsValidationPanel.vue'
import FlValidationPanel from '@/components/validation/FlValidationPanel.vue'
import BreathTrendsPanel from '@/components/validation/BreathTrendsPanel.vue'
import ReraValidationPanel from '@/components/validation/ReraValidationPanel.vue'
import AppleCrossPanel from '@/components/validation/AppleCrossPanel.vue'
import RunComparison from '@/components/validation/RunComparison.vue'
import ValidationRunHistory from '@/components/validation/ValidationRunHistory.vue'
import { VALIDATOR_LABELS } from '@/utils/validationMetrics'
import type { ValidatorType, ValidationRunStatus } from '@/types'

const TABS: ValidatorType[] = ['events', 'fl', 'breaths', 'rera', 'apple']

const route = useRoute()
const router = useRouter()

function isTab(value: unknown): value is ValidatorType {
    return typeof value === 'string' && (TABS as string[]).includes(value)
}

const activeTab = ref<ValidatorType>(isTab(route.query.tab) ? route.query.tab : 'events')

// Keep the active tab addressable via ?tab=<validator>.
watch(activeTab, (tab) => {
    if (route.query.tab !== tab) {
        void router.replace({ query: { ...route.query, tab } })
    }
})
watch(
    () => route.query.tab,
    (tab) => {
        if (isTab(tab) && tab !== activeTab.value) activeTab.value = tab
    },
)

// A run selected from History loads into its matching tab.
const pendingLoad = ref<{ type: ValidatorType; runId: number } | null>(null)

function loadRunFor(type: ValidatorType): number | null {
    return pendingLoad.value?.type === type ? pendingLoad.value.runId : null
}

function onSelectRun(run: ValidationRunStatus): void {
    const type = run.validator_type as ValidatorType
    pendingLoad.value = { type, runId: run.run_id }
    activeTab.value = type
}

onMounted(() => {
    if (!isTab(route.query.tab)) {
        void router.replace({ query: { ...route.query, tab: activeTab.value } })
    }
})
</script>
