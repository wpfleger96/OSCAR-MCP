<template>
    <aside class="app-sidebar">
        <div class="sidebar-header">
            <span class="sidebar-logo">SNORE</span>
        </div>
        <nav class="sidebar-nav">
            <RouterLink to="/" class="nav-item">
                <i class="pi pi-chart-bar" />
                <span>Dashboard</span>
            </RouterLink>
            <RouterLink to="/sessions" class="nav-item">
                <i class="pi pi-list" />
                <span>Sessions</span>
            </RouterLink>
            <RouterLink to="/stats" class="nav-item">
                <i class="pi pi-chart-line" />
                <span>Stats</span>
            </RouterLink>
            <RouterLink to="/rx" class="nav-item">
                <i class="pi pi-cog" />
                <span>RX History</span>
            </RouterLink>
        </nav>
    </aside>
</template>

<style scoped>
.app-sidebar {
    background: var(--p-surface-card, #ffffff);
    border-right: 1px solid var(--p-surface-border, #e2e8f0);
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 0;
    height: 100vh;
}

.sidebar-header {
    padding: 1.25rem 1rem;
    border-bottom: 1px solid var(--p-surface-border, #e2e8f0);
}

.sidebar-logo {
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--p-primary-color, #3b82f6);
}

.sidebar-nav {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.75rem 0.5rem;
}

.nav-item {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    padding: 0.6rem 0.75rem;
    border-radius: 6px;
    font-size: 0.9rem;
    text-decoration: none;
    color: var(--p-text-color, #1a1a1a);
    transition: background 0.15s;
    cursor: pointer;
}

.nav-item:hover {
    background: var(--p-surface-hover, #f1f5f9);
}

.router-link-active {
    background: var(--p-primary-50, #eff6ff);
    color: var(--p-primary-color, #3b82f6);
    font-weight: 500;
}
</style>
