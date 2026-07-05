<template>
    <div class="app-layout">
        <AppSidebar class="hidden md:flex" />
        <main class="app-main">
            <button
                class="md:hidden fixed top-3 left-3 z-50 inline-flex items-center justify-center rounded-md border border-border bg-background p-2 text-foreground shadow-sm"
                @click="mobileMenuOpen = true"
            >
                <Menu class="h-5 w-5" />
            </button>
            <RouterView />
        </main>
    </div>
    <Sheet v-model:open="mobileMenuOpen">
        <SheetContent side="left" class="w-[220px] p-0">
            <SheetTitle class="sr-only">Navigation</SheetTitle>
            <AppSidebar />
        </SheetContent>
    </Sheet>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Menu } from '@lucide/vue'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import AppSidebar from '@/components/AppSidebar.vue'

const mobileMenuOpen = ref(false)
const route = useRoute()

watch(
    () => route.path,
    () => {
        mobileMenuOpen.value = false
    },
)
</script>

<style>
/* No universal reset here: Tailwind preflight already zeroes margin/padding in
   @layer base, and an unlayered duplicate overrides every layered
   margin/padding utility (p-*, px-*, m-*) app-wide. */
.app-layout {
    display: grid;
    grid-template-columns: 220px 1fr;
    min-height: 100vh;
}

.app-main {
    padding: 1.5rem;
    overflow-y: auto;
    min-width: 0;
}

@media (max-width: 767px) {
    .app-layout {
        grid-template-columns: 1fr;
    }

    .app-main {
        padding-top: 3.5rem;
    }
}
</style>
