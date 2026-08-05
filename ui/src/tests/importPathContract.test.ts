import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { ref } from 'vue'

// Mock api.post so the real importFromPath runs but HTTP never fires.
vi.mock('@/api/client', () => ({
    default: { post: vi.fn() },
    apiPost: () => vi.fn(),
    apiGet: () => vi.fn(),
    apiPatch: () => vi.fn(),
    apiDelete: () => vi.fn(),
    apiGetOrNull: () => vi.fn(),
    createApiEndpoint: () => vi.fn(),
}))
vi.mock('@/composables/useAuth')
// Keep real importFromPath; mock only the functions not under test.
vi.mock('@/api/import', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/api/import')>()
    return { ...actual, detectSources: vi.fn(), importFiles: vi.fn() }
})
vi.mock('@/api/sse', () => ({ connectImportProgress: vi.fn(), cancelImport: vi.fn() }))
vi.mock('@/composables/useDarkMode', () => ({
    useDarkMode: () => ({ isDark: ref(false), toggleDark: vi.fn() }),
}))
vi.mock('@/utils/formatting', () => ({ formatBytes: (n: number) => `${n}B` }))

import api from '@/api/client'
import { useAuth } from '@/composables/useAuth'

describe('wire contract: importFromPath sends profile_id in HTTP body', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(api.post).mockResolvedValue({ data: { job_id: 'job-1' } })
        vi.mocked(useAuth).mockReturnValue({
            isAuthenticated: ref(true) as never,
            isLocal: ref(false) as never,
            fetchStatus: vi.fn().mockResolvedValue(undefined),
            refreshStatus: vi.fn().mockResolvedValue(undefined),
            user: ref({ id: 1, email: 'u@x.com', display_name: 'U', role: 'user' }) as never,
            profiles: ref([
                { id: 1, name: 'Primary' },
                { id: 2, name: 'Work' },
            ]) as never,
            activeProfileId: ref(1) as never,
            authMode: ref('multiuser') as never,
            profileKey: ref(0) as never,
            role: ref(null) as never,
            canWrite: ref(true) as never,
            login: vi.fn(),
            demoLogin: vi.fn(),
            logout: vi.fn(),
            clearAuth: vi.fn(),
            setActiveProfile: vi.fn(),
        })
    })

    it('test_handlePathImport_sends_profile_id_not_target_profile_id', async () => {
        // Falsifiable: rename profile_id → target_profile_id in api/import.ts line 26;
        // api.post receives { target_profile_id: 2 } and objectContaining({ profile_id: 2 }) fails.
        const ImportView = (await import('@/views/ImportView.vue')).default
        const testRouter = createRouter({
            history: createWebHistory(),
            routes: [{ path: '/import', component: ImportView }],
        })
        await testRouter.push('/import')
        const wrapper = mount(ImportView, { global: { plugins: [testRouter] } })

        const vm = wrapper.vm as unknown as Record<string, unknown>
        // Pre-populate path-import state so the early-return guard passes
        vm['detectedSources'] = [{ parser_name: 'resmed', root_path: '/data/sd' }]
        vm['selectedSources'] = new Set([0])
        // Select profile 2 — different from active profile 1
        vm['selectedProfileId'] = 2

        await (vm as { handlePathImport: () => Promise<void> }).handlePathImport()

        expect(api.post).toHaveBeenCalledWith(
            '/import/path',
            expect.objectContaining({ profile_id: 2 }),
        )
    })
})
