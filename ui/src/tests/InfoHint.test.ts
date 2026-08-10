import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Stub the Popover family so tests run without DOM teleport / floating-ui APIs.
vi.mock('@/components/ui/popover', () => ({
    Popover: { template: '<div class="popover-stub"><slot /></div>' },
    PopoverTrigger: {
        props: ['asChild'],
        template: '<div class="popover-trigger-stub"><slot /></div>',
    },
    PopoverContent: { template: '<div class="popover-content-stub"><slot /></div>' },
    PopoverHeader: { template: '<div><slot /></div>' },
    PopoverTitle: { template: '<span class="popover-title-stub"><slot /></span>' },
    PopoverDescription: { template: '<p class="popover-description-stub"><slot /></p>' },
}))

// Stub the lucide icon to avoid SVG rendering issues.
vi.mock('@lucide/vue', () => ({
    Info: { template: '<svg class="icon-info-stub" />' },
}))

import InfoHint from '@/components/InfoHint.vue'

describe('InfoHint', () => {
    it('test_valid_glossary_key_resolves_label_in_aria', () => {
        const wrapper = mount(InfoHint, { props: { glossaryKey: 'ahi' } })

        const trigger = wrapper.find('button[type="button"]')
        expect(trigger.attributes('aria-label')).toContain('AHI')
    })

    it('test_valid_glossary_key_renders_short_text', () => {
        const wrapper = mount(InfoHint, { props: { glossaryKey: 'ahi' } })

        expect(wrapper.find('.popover-description-stub').text()).toContain('Apnea-Hypopnea Index')
    })

    it('test_unknown_glossary_key_does_not_throw_and_renders_empty_label', () => {
        // Should not throw; aria-label should end with empty resolved label.
        let wrapper: ReturnType<typeof mount> | undefined
        expect(() => {
            wrapper = mount(InfoHint, { props: { glossaryKey: 'nonexistent_key_xyz' } })
        }).not.toThrow()

        const trigger = wrapper!.find('button[type="button"]')
        expect(trigger.attributes('aria-label')).toBe('More information about ')
    })

    it('test_explicit_label_overrides_glossary_entry', () => {
        const wrapper = mount(InfoHint, {
            props: { glossaryKey: 'ahi', label: 'My Custom Label' },
        })

        const trigger = wrapper.find('button[type="button"]')
        expect(trigger.attributes('aria-label')).toContain('My Custom Label')
        expect(trigger.attributes('aria-label')).not.toContain('AHI')
    })

    it('test_explicit_short_overrides_glossary_entry', () => {
        const wrapper = mount(InfoHint, {
            props: { glossaryKey: 'ahi', short: 'Custom short text.' },
        })

        expect(wrapper.find('.popover-description-stub').text()).toBe('Custom short text.')
    })
})
