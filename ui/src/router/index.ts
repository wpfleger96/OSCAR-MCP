import { createRouter, createWebHistory } from 'vue-router'

function sessionIdProp(route: { params: { id: string | string[] } }) {
    const id = Number(route.params.id)
    return { sessionId: Number.isFinite(id) ? id : -1 }
}

const router = createRouter({
    history: createWebHistory(),
    routes: [
        {
            path: '/',
            name: 'dashboard',
            component: () => import('@/views/DashboardView.vue'),
        },
        {
            path: '/sessions',
            name: 'sessions',
            component: () => import('@/views/SessionListView.vue'),
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
    ],
})

export default router
