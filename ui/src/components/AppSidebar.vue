<template>
    <aside class="app-sidebar">
        <div class="sidebar-header">
            <span class="sidebar-logo">SNORE</span>
        </div>
        <nav class="sidebar-nav">
            <span class="nav-group-label">Data</span>
            <RouterLink to="/" class="nav-item">
                <BarChart3 class="h-4 w-4" />
                <span>Dashboard</span>
            </RouterLink>
            <RouterLink to="/sessions" class="nav-item">
                <List class="h-4 w-4" />
                <span>Sessions</span>
            </RouterLink>
            <RouterLink to="/devices" class="nav-item">
                <HardDrive class="h-4 w-4" />
                <span>Devices</span>
            </RouterLink>

            <span class="nav-group-label">Analysis</span>
            <RouterLink to="/analysis" class="nav-item">
                <Brain class="h-4 w-4" />
                <span>Analysis</span>
            </RouterLink>
            <RouterLink to="/validation" class="nav-item">
                <CheckCircle class="h-4 w-4" />
                <span>Validation</span>
            </RouterLink>

            <span class="nav-group-label">Tools</span>
            <RouterLink to="/import" class="nav-item">
                <Upload class="h-4 w-4" />
                <span>Import</span>
            </RouterLink>
            <RouterLink to="/export" class="nav-item">
                <Download class="h-4 w-4" />
                <span>Export</span>
            </RouterLink>
            <RouterLink to="/reports" class="nav-item">
                <FileText class="h-4 w-4" />
                <span>Reports</span>
            </RouterLink>
            <RouterLink to="/database" class="nav-item">
                <Database class="h-4 w-4" />
                <span>Database</span>
            </RouterLink>

            <span class="nav-group-label">Settings</span>
            <RouterLink to="/stats" class="nav-item">
                <TrendingUp class="h-4 w-4" />
                <span>Stats</span>
            </RouterLink>
            <RouterLink to="/rx" class="nav-item">
                <Pill class="h-4 w-4" />
                <span>RX History</span>
            </RouterLink>
        </nav>
        <div class="sidebar-footer">
            <button class="nav-item" @click="toggleDark">
                <Sun v-if="isDark" class="h-4 w-4" />
                <Moon v-else class="h-4 w-4" />
                <span>{{ isDark ? 'Light Mode' : 'Dark Mode' }}</span>
            </button>
        </div>
    </aside>
</template>

<script setup lang="ts">
import {
    BarChart3,
    Brain,
    CheckCircle,
    Database,
    Download,
    FileText,
    HardDrive,
    List,
    Moon,
    Pill,
    Sun,
    TrendingUp,
    Upload,
} from '@lucide/vue'
import { useDarkMode } from '@/composables/useDarkMode'

const { isDark, toggleDark } = useDarkMode()
</script>

<style scoped>
.app-sidebar {
    background: var(--color-card);
    border-right: 1px solid var(--color-border);
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
}

.sidebar-header {
    padding: 1.25rem 1rem;
    border-bottom: 1px solid var(--color-border);
}

.sidebar-logo {
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--color-primary);
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
    color: var(--color-foreground);
    transition: background 0.15s;
    cursor: pointer;
    background: none;
    border: none;
    width: 100%;
    text-align: left;
}

.nav-item:hover {
    background: var(--color-accent);
}

.router-link-active {
    background: hsl(from var(--color-primary) h s l / 0.1);
    color: var(--color-primary);
    font-weight: 500;
}

.sidebar-footer {
    margin-top: auto;
    padding: 0.75rem 0.5rem;
    border-top: 1px solid var(--color-border);
}

.nav-group-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-muted-foreground);
    padding: 0.75rem 0.75rem 0.25rem;
}

.nav-group-label:first-child {
    padding-top: 0;
}
</style>
