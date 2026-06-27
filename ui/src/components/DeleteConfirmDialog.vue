<template>
    <AlertDialog :open="visible" @update:open="$emit('update:visible', $event)">
        <AlertDialogContent class="max-w-[450px]">
            <AlertDialogHeader>
                <AlertDialogTitle>{{ title }}</AlertDialogTitle>
                <div class="text-sm text-muted-foreground">
                    <div v-if="loading" class="py-8 text-center">
                        <Loader2 class="inline h-4 w-4 animate-spin" /> Loading preview...
                    </div>
                    <div v-else>
                        <p class="mb-4 text-foreground">{{ message }}</p>
                        <slot name="preview" />
                    </div>
                </div>
            </AlertDialogHeader>
            <AlertDialogFooter>
                <AlertDialogCancel @click="$emit('update:visible', false)"
                    >Cancel</AlertDialogCancel
                >
                <AlertDialogAction as-child>
                    <Button
                        variant="destructive"
                        :disabled="loading || deleting"
                        @click="$emit('confirm')"
                    >
                        <Loader2 v-if="deleting" class="mr-2 h-4 w-4 animate-spin" />
                        <Trash2 v-else class="mr-2 h-4 w-4" />
                        Delete
                    </Button>
                </AlertDialogAction>
            </AlertDialogFooter>
        </AlertDialogContent>
    </AlertDialog>
</template>

<script setup lang="ts">
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Loader2, Trash2 } from '@lucide/vue'

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
