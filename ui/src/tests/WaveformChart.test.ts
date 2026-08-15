import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// uPlot must be mocked before the component is imported.
// Vitest requires a 'function' or 'class' body for constructor mocks.
vi.mock('uplot', () => {
    function UPlotMock(this: Record<string, unknown>) {
        this.destroy = vi.fn()
        this.setData = vi.fn()
        this.setSize = vi.fn()
        this.setScale = vi.fn()
        this.batch = vi.fn()
        this.over = document.createElement('div')
        this.scales = {}
        this.height = 240
        this.redraw = vi.fn()
    }
    ;(UPlotMock as unknown as Record<string, unknown>).sync = vi.fn(() => ({
        key: 'k',
        sub: vi.fn(),
        unsub: vi.fn(),
    }))
    ;(UPlotMock as unknown as Record<string, unknown>).pxRatio = 1
    return { default: UPlotMock }
})

// Stub lucide icons used inside WaveformChart.
vi.mock('@lucide/vue', () => ({
    Loader2: { template: '<svg class="loader2-stub animate-spin" />' },
}))

// useDarkMode must return a real Vue ref so the watch() inside WaveformChart works.
vi.mock('@/composables/useDarkMode', async () => {
    const { ref } = await vi.importActual<typeof import('vue')>('vue')
    return {
        useDarkMode: () => ({ isDark: ref(false) }),
    }
})

// Stub formatting utility.
vi.mock('@/utils/formatting', () => ({
    formatWallClockTime: (v: number) => String(v),
}))

// Stub ResizeObserver (jsdom does not provide it).
// Vitest requires a function or class body for constructor mocks.
beforeEach(() => {
    ;(globalThis as unknown as Record<string, unknown>).ResizeObserver = vi.fn(
        function ResizeObserverMock(this: Record<string, unknown>) {
            this.observe = vi.fn()
            this.unobserve = vi.fn()
            this.disconnect = vi.fn()
        },
    )
})

import WaveformChart from '@/components/WaveformChart.vue'

const MINIMAL_PROPS = {
    timestamps: [0, 1],
    values: [0, 1],
    unit: 'L/min',
    label: 'Flow',
    startEpoch: 0,
}

describe('WaveformChart', () => {
    it('test_sanity_mounts_without_errors_given_minimal_props', () => {
        expect(() => mount(WaveformChart, { props: MINIMAL_PROPS })).not.toThrow()
    })

    it('test_refetching_true_renders_corner_spinner', () => {
        const wrapper = mount(WaveformChart, {
            props: { ...MINIMAL_PROPS, refetching: true },
        })

        expect(wrapper.find('.animate-spin').exists()).toBe(true)
    })

    it('test_refetching_false_hides_corner_spinner', () => {
        const wrapper = mount(WaveformChart, {
            props: { ...MINIMAL_PROPS, refetching: false },
        })

        expect(wrapper.find('.animate-spin').exists()).toBe(false)
    })

    it('test_refetching_omitted_hides_corner_spinner', () => {
        const wrapper = mount(WaveformChart, {
            props: MINIMAL_PROPS,
        })

        expect(wrapper.find('.animate-spin').exists()).toBe(false)
    })
})
