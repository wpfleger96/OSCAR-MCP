<template>
    <div class="app-layout">
        <AppSidebar />
        <main class="app-main">
            <RouterView />
        </main>
    </div>
</template>

<script setup lang="ts">
import AppSidebar from '@/components/AppSidebar.vue'
</script>

<style>
*,
*::before,
*::after {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: var(--p-font-family, system-ui, sans-serif);
    background: var(--p-surface-ground, #f8f9fa);
    color: var(--p-text-color, #1a1a1a);
}

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
</style>
