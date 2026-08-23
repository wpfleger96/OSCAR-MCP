<script setup lang="ts">
import type { TabsListProps } from 'reka-ui'
import type { HTMLAttributes } from 'vue'
import { reactiveOmit } from '@vueuse/core'
import { TabsList } from 'reka-ui'
import { cn } from '@/lib/utils'

const props = defineProps<TabsListProps & { class?: HTMLAttributes['class'] }>()
const delegatedProps = reactiveOmit(props, 'class')
</script>

<template>
    <TabsList
        data-slot="tabs-list"
        v-bind="delegatedProps"
        :class="
            cn(
                'inline-flex w-fit max-w-full items-center justify-start gap-1 overflow-x-auto rounded-lg bg-muted p-[3px] text-muted-foreground',
                props.class,
            )
        "
    >
        <slot />
    </TabsList>
</template>
