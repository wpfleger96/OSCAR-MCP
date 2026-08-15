<template>
    <nav class="mobile-tab-bar">
        <RouterLink
            v-for="tab in tabs"
            :key="tab.to"
            :to="tab.to"
            class="tab-item"
            :class="{ 'tab-item--active': isTabActive(tab.to) }"
        >
            <component :is="tab.icon" class="h-5 w-5" />
            <span>{{ tab.label }}</span>
        </RouterLink>
        <button
            class="tab-item"
            :class="{ 'tab-item--active': moreActive }"
            type="button"
            @click="emit('more')"
        >
            <Menu class="h-5 w-5" />
            <span>More</span>
        </button>
    </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { BarChart3, List, Menu, Moon, TrendingUp } from '@lucide/vue'

const emit = defineEmits<{ more: [] }>()

const route = useRoute()

const tabs = [
    { to: '/dashboard', icon: BarChart3, label: 'Dashboard' },
    { to: '/sessions', icon: List, label: 'Sessions' },
    { to: '/stats', icon: TrendingUp, label: 'Stats' },
    { to: '/apple-health', icon: Moon, label: 'Health' },
]

// Prefix matching by path, not `router-link-active`: detail routes like
// /sessions/:id are sibling top-level route records, so record-based matching
// would leave no tab highlighted there.
function isTabActive(to: string): boolean {
    return route.path === to || route.path.startsWith(to + '/')
}

// The More button is "active" when no tab owns the current route.
const moreActive = computed(() => !tabs.some((tab) => isTabActive(tab.to)))
</script>

<style scoped>
.mobile-tab-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 40;
    display: flex;
    background: var(--color-card);
    border-top: 1px solid var(--color-border);
    padding-bottom: env(safe-area-inset-bottom, 0px);
    padding-left: env(safe-area-inset-left, 0px);
    padding-right: env(safe-area-inset-right, 0px);
}

.tab-item {
    flex: 1;
    min-width: 44px;
    height: 3.5rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.125rem;
    font-size: 0.65rem;
    text-decoration: none;
    color: var(--color-muted-foreground);
    background: none;
    border: none;
    cursor: pointer;
}

.tab-item--active {
    color: var(--color-primary);
}

/* Desktop hiding lives here, not in a parent `md:hidden` utility — this scoped
   block is unlayered and its `display: flex` would beat any layered utility. */
@media (min-width: 768px) {
    .mobile-tab-bar {
        display: none;
    }
}
</style>
