<template>
    <div v-if="!route.meta.authFree" class="app-layout">
        <AppSidebar />
        <main class="app-main">
            <RouterView :key="profileKey" />
        </main>
    </div>
    <main v-else class="auth-layout">
        <RouterView :key="profileKey" />
    </main>
    <MobileTabBar v-if="!route.meta.authFree" @more="mobileMenuOpen = true" />
    <Sheet v-if="!route.meta.authFree" v-model:open="mobileMenuOpen">
        <SheetContent side="left" class="w-[220px] p-0">
            <SheetTitle class="sr-only">Navigation</SheetTitle>
            <AppSidebar />
        </SheetContent>
    </Sheet>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import AppSidebar from '@/components/AppSidebar.vue'
import MobileTabBar from '@/components/MobileTabBar.vue'
import { useAuth } from '@/composables/useAuth'
import { useIsMobile } from '@/composables/useIsMobile'

const mobileMenuOpen = ref(false)
const route = useRoute()
const { profileKey } = useAuth()
const { isMobile } = useIsMobile()

watch(
    () => route.path,
    () => {
        mobileMenuOpen.value = false
    },
)

// Close the mobile sheet if the viewport grows past the mobile breakpoint —
// the route watcher above only fires on navigation, not on resize.
watch(isMobile, (mobile) => {
    if (!mobile) mobileMenuOpen.value = false
})
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

.auth-layout {
    min-height: 100vh;
}

@media (max-width: 767.98px) {
    .app-layout {
        grid-template-columns: 1fr;
    }

    /* !important: AppSidebar's scoped `display: flex` is unlayered too, so a
       Tailwind `hidden` utility (layered) can never win — this must be forced. */
    .app-layout > .app-sidebar {
        display: none !important;
    }

    .app-main {
        padding-top: 1rem;
        padding-bottom: calc(4.5rem + env(safe-area-inset-bottom, 0px));
        padding-left: max(1rem, env(safe-area-inset-left, 0px));
        padding-right: max(1rem, env(safe-area-inset-right, 0px));
        overflow-x: clip;
    }
}
</style>
