import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
    history: createWebHistory(),
    routes: [
        {
            path: '/',
            redirect: '/sessions',
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
            props: (route) => {
                const id = Number(route.params.id)
                return { sessionId: Number.isFinite(id) ? id : -1 }
            },
        },
    ],
})

export default router
