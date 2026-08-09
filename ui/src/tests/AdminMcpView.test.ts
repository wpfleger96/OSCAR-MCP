import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/admin')

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button><slot /></button>' },
}))

import AdminMcpView from '@/views/AdminMcpView.vue'
import { getMcpStatus } from '@/api/admin'

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const ENABLED_STATUS = {
    enabled: true,
    endpoint_url: 'https://mcp.snoreapp.ai/mcp',
    transport: 'streamable-http',
    auth_provider: 'google',
    disabled_reason: null,
    linked_google_identities: 3,
}

const DISABLED_STATUS = {
    enabled: false,
    endpoint_url: null,
    transport: null,
    auth_provider: null,
    disabled_reason: 'Google OAuth not configured',
    linked_google_identities: 0,
}

async function mountAndLoad() {
    const wrapper = mount(AdminMcpView)
    await flushPromises()
    return wrapper
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AdminMcpView', () => {
    beforeEach(() => {
        vi.resetAllMocks()
    })

    it('test_enabled_status_shows_badge_endpoint_and_identity_count', async () => {
        vi.mocked(getMcpStatus).mockResolvedValue(ENABLED_STATUS)

        const wrapper = await mountAndLoad()

        expect(wrapper.find('.status-badge--active').text()).toBe('Enabled')

        const endpointInput = wrapper.find('input[readonly]')
        expect(endpointInput.exists()).toBe(true)
        expect((endpointInput.element as HTMLInputElement).value).toBe(
            'https://mcp.snoreapp.ai/mcp',
        )

        expect(wrapper.text()).toContain('streamable-http')
        expect(wrapper.text()).toContain('google')
        expect(wrapper.text()).toContain('3')
    })

    it('test_disabled_status_shows_reason_and_hides_endpoint_card', async () => {
        vi.mocked(getMcpStatus).mockResolvedValue(DISABLED_STATUS)

        const wrapper = await mountAndLoad()

        expect(wrapper.find('.status-badge--disabled').text()).toBe('Disabled')
        expect(wrapper.text()).toContain('Google OAuth not configured')
        expect(wrapper.find('input[readonly]').exists()).toBe(false)
    })

    it('test_load_failure_shows_error_state', async () => {
        vi.mocked(getMcpStatus).mockRejectedValue(new Error('Forbidden'))

        const wrapper = await mountAndLoad()

        expect(wrapper.find('.error-state').text()).toContain('Forbidden')
        expect(wrapper.find('.section-card').exists()).toBe(false)
    })
})
