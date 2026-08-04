import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

function sessionIdProp(route: { params: { id: string | string[] } }) {
    const id = Number(route.params.id)
    return { sessionId: Number.isFinite(id) ? id : -1 }
}

const router = createRouter({
    history: createWebHistory(),
    routes: [
        // Auth-free routes
        {
            path: '/',
            name: 'login',
            component: () => import('@/views/LoginView.vue'),
        },
        {
            path: '/invite/:token',
            name: 'invite',
            component: () => import('@/views/InviteView.vue'),
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

// Auth-free paths: login page and invite redemption.
const AUTH_FREE = ['/', '/invite']

router.beforeEach(async (to) => {
    const { fetchStatus, isAuthenticated, isLocal } = useAuth()

    try {
        await fetchStatus()
    } catch {
        // Network failure — allow through (server will return 401 on data requests).
    }

    const authed = isAuthenticated.value || isLocal.value

    // Unauthenticated user trying to reach a guarded route → login.
    if (!authed && !AUTH_FREE.some((p) => to.path === p || to.path.startsWith('/invite/'))) {
        return '/'
    }

    // Authenticated user landing on login page → dashboard.
    if (authed && to.path === '/') {
        return '/dashboard'
    }
})

export default router
