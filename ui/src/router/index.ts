import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationNormalized } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

declare module 'vue-router' {
    interface RouteMeta {
        authFree?: boolean
    }
}

function sessionIdProp(route: { params: { id: string | string[] } }) {
    const id = Number(route.params.id)
    return { sessionId: Number.isFinite(id) ? id : -1 }
}

const router = createRouter({
    history: createWebHistory(),
    routes: [
        // Auth-free routes — no sidebar, no navigation chrome.
        {
            path: '/',
            name: 'login',
            component: () => import('@/views/LoginView.vue'),
            meta: { authFree: true },
        },
        {
            path: '/invite',
            name: 'invite',
            component: () => import('@/views/InviteView.vue'),
            meta: { authFree: true },
        },

        // Authenticated routes
        {
            path: '/dashboard',
            name: 'dashboard',
            component: () => import('@/views/DashboardView.vue'),
        },
        {
            path: '/profiles',
            name: 'profiles',
            component: () => import('@/views/ProfilesView.vue'),
        },
        {
            path: '/sessions',
            name: 'sessions',
            component: () => import('@/views/SessionListView.vue'),
        },
        {
            path: '/devices',
            name: 'devices',
            component: () => import('@/views/DevicesView.vue'),
        },
        {
            path: '/sessions/:id',
            name: 'session-detail',
            component: () => import('@/views/SessionDetailView.vue'),
            props: sessionIdProp,
        },
        {
            path: '/sessions/:id/events',
            name: 'session-events',
            component: () => import('@/views/EventExplorerView.vue'),
            props: sessionIdProp,
        },
        {
            path: '/sessions/:id/analysis',
            name: 'session-analysis',
            component: () => import('@/views/AnalysisView.vue'),
            props: sessionIdProp,
        },
        {
            path: '/stats',
            name: 'stats',
            component: () => import('@/views/StatsView.vue'),
        },
        {
            path: '/rx',
            name: 'rx-history',
            component: () => import('@/views/RxHistoryView.vue'),
        },
        {
            path: '/import',
            name: 'import',
            component: () => import('@/views/ImportView.vue'),
        },
        {
            path: '/export',
            name: 'export',
            component: () => import('@/views/ExportView.vue'),
        },
        {
            path: '/reports',
            name: 'reports',
            component: () => import('@/views/ReportsView.vue'),
        },
        {
            path: '/analysis',
            name: 'analysis-management',
            component: () => import('@/views/AnalysisManagementView.vue'),
        },
        {
            path: '/database',
            name: 'database',
            component: () => import('@/views/DatabaseView.vue'),
        },
        {
            path: '/validation',
            name: 'validation',
            component: () => import('@/views/ValidationView.vue'),
        },
        {
            path: '/days/:date',
            name: 'day-detail',
            component: () => import('@/views/DayDetailView.vue'),
            props: (route: { params: { date: string | string[] } }) => ({
                dayDate: Array.isArray(route.params.date)
                    ? route.params.date[0]
                    : route.params.date,
            }),
        },
    ],
})

// Exported for unit testing — the production router uses this directly.
export async function authGuard(to: RouteLocationNormalized): Promise<string | boolean | void> {
    const { fetchStatus, isAuthenticated, isLocal } = useAuth()

    try {
        await fetchStatus()
    } catch {
        // Network failure — allow through (data endpoints will 401 if session is bad).
    }

    const authed = isAuthenticated.value || isLocal.value

    // Unauthenticated user on a guarded route → login.
    if (!authed && !to.meta.authFree) {
        return '/'
    }

    // Authenticated user landing on login page → dashboard.
    if (authed && to.path === '/') {
        return '/dashboard'
    }
}

router.beforeEach(authGuard)

export default router
