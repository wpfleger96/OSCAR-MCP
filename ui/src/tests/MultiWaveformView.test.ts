import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// uPlot is called at module scope (uPlot.sync(...)); mock it before importing the component.
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

vi.mock('@/api/waveforms')

// Stub child components that pull in uPlot or complex UI.
vi.mock('@/components/WaveformChart.vue', () => ({
    default: {
        name: 'WaveformChart',
        props: [
            'timestamps',
            'values',
            'unit',
            'label',
            'waveformType',
            'syncKey',
            'startEpoch',
            'refetching',
            'events',
        ],
        emits: ['zoom'],
        template: '<div class="waveform-chart-stub" :data-refetching="refetching" />',
    },
}))

vi.mock('@/components/InfoHint.vue', () => ({
    default: { template: '<span class="info-hint-stub" />' },
}))

vi.mock('@/components/ui/select', () => ({
    Select: {
        template: '<div><slot /></div>',
        props: ['modelValue'],
        emits: ['update:modelValue'],
    },
    SelectTrigger: { template: '<div><slot /></div>' },
    SelectValue: { template: '<span />' },
    SelectContent: { template: '<div><slot /></div>' },
    SelectItem: { template: '<div><slot /></div>', props: ['value'] },
}))

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

vi.mock('@lucide/vue', () => ({
    X: { template: '<svg class="icon-x" />' },
    Loader2: { template: '<svg class="loader2-stub animate-spin" />' },
    AlertTriangle: { template: '<svg class="icon-alert-triangle" />' },
}))

import { getWaveformData } from '@/api/waveforms'
import MultiWaveformView from '@/components/MultiWaveformView.vue'
import type { WaveformDataResponse } from '@/types'

const mockGetWaveformData = vi.mocked(getWaveformData)

function makeResponse(overrides: Partial<WaveformDataResponse> = {}): WaveformDataResponse {
    return {
        timestamps: [0, 1, 2],
        values: [1.0, 1.5, 2.0],
        unit: 'L/min',
        downsampled: false,
        returned_samples: 3,
        sample_rate: 1,
        total_samples: 3,
        ...overrides,
    }
}

const DEFAULT_PROPS = {
    sessionId: 42,
    availableTypes: ['flow', 'pressure'],
    initialTypes: ['flow'],
    startEpoch: 0,
}

beforeEach(() => {
    vi.resetAllMocks()
})

describe('MultiWaveformView', () => {
    it('test_initial_load_in_flight_shows_full_height_spinner_and_no_chart', async () => {
        // Arrange: deferred promise — load never resolves during this test
        let _resolve!: (v: WaveformDataResponse) => void
        const deferred = new Promise<WaveformDataResponse>((res) => {
            _resolve = res
        })
        mockGetWaveformData.mockReturnValue(deferred)

        // Act
        const wrapper = mount(MultiWaveformView, { props: DEFAULT_PROPS })
        await Promise.resolve() // allow onMounted to run

        // Assert
        expect(wrapper.find('.animate-spin').exists()).toBe(true)
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(false)

        // Cleanup
        _resolve(makeResponse())
        await flushPromises()
    })

    it('test_load_resolves_shows_chart_and_hides_spinner', async () => {
        // Arrange
        mockGetWaveformData.mockResolvedValue(makeResponse())

        // Act
        const wrapper = mount(MultiWaveformView, { props: DEFAULT_PROPS })
        await flushPromises()

        // Assert
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(true)
        expect(wrapper.find('.animate-spin').exists()).toBe(false)
    })

    it('test_zoomTo_keeps_chart_mounted_with_refetching_true_then_false_after_resolve', async () => {
        // Arrange: initial load resolves immediately
        mockGetWaveformData.mockResolvedValueOnce(makeResponse())

        const wrapper = mount(MultiWaveformView, { props: DEFAULT_PROPS })
        await flushPromises()

        // Sanity: chart rendered
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(true)

        // Now deferred zoom refetch
        let resolveZoom!: (v: WaveformDataResponse) => void
        const zoomDeferred = new Promise<WaveformDataResponse>((res) => {
            resolveZoom = res
        })
        mockGetWaveformData.mockReturnValueOnce(zoomDeferred)

        // Act: call zoomTo via exposed
        const vm = wrapper.vm as unknown as { zoomTo: (s: number, e: number) => void }
        vm.zoomTo(10, 20)
        await Promise.resolve()

        // Assert: chart stays mounted, refetching=true, no full-height spinner
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(true)
        expect(wrapper.find('.waveform-chart-stub').attributes('data-refetching')).toBe('true')
        expect(wrapper.find('.animate-spin').exists()).toBe(false)

        // Resolve zoom and verify refetching clears
        resolveZoom(makeResponse())
        await flushPromises()

        expect(wrapper.find('.waveform-chart-stub').attributes('data-refetching')).toBe('false')
    })

    it('test_zoom_refetch_reject_keeps_chart_mounted_and_shows_banner', async () => {
        // Arrange: initial load resolves, zoom refetch rejects
        mockGetWaveformData
            .mockResolvedValueOnce(makeResponse())
            .mockRejectedValueOnce(new Error('timeout'))

        const wrapper = mount(MultiWaveformView, { props: DEFAULT_PROPS })
        await flushPromises()

        // Act: trigger zoom via zoomTo
        const vm = wrapper.vm as unknown as { zoomTo: (s: number, e: number) => void }
        vm.zoomTo(10, 20)
        await flushPromises()

        // Assert: chart still rendered, error banner shown, no full-height error block
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(true)
        expect(wrapper.find('.h-60').exists()).toBe(false)
        expect(wrapper.text()).toContain('Failed to refresh waveform: timeout')
    })

    it('test_initial_load_rejects_shows_error_block_and_no_chart', async () => {
        // Arrange
        mockGetWaveformData.mockRejectedValue(new Error('server error'))

        // Act
        const wrapper = mount(MultiWaveformView, { props: DEFAULT_PROPS })
        await flushPromises()

        // Assert: error block shown, chart absent
        expect(wrapper.find('.waveform-chart-stub').exists()).toBe(false)
        expect(wrapper.text()).toContain('server error')
    })
})
