import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'

// All vi.mock calls must appear before any component import.

vi.mock('@/composables/useAuth')

vi.mock('@/api/equipment', () => ({
    getMaskLog: vi.fn(),
    createMaskLogEntry: vi.fn(),
    updateMaskLogEntry: vi.fn(),
    deleteMaskLogEntry: vi.fn(),
}))

// Return dates as-is so test assertions can use ISO strings directly.
vi.mock('@/utils/formatting', () => ({
    formatDateFull: (d: string) => d,
}))

vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button v-bind="$attrs"><slot /></button>' },
}))

vi.mock('@/components/ui/select', () => ({
    Select: defineComponent({
        props: ['modelValue'],
        emits: ['update:model-value'],
        template: '<div><slot /></div>',
    }),
    SelectContent: { template: '<div><slot /></div>' },
    SelectItem: { props: ['value'], template: '<div><slot /></div>' },
    SelectTrigger: { template: '<div><slot /></div>' },
    SelectValue: { props: ['placeholder'], template: '<span />' },
}))

vi.mock('@lucide/vue', () => ({
    Loader2: { template: '<svg />' },
}))

vi.mock('@/components/DatePickerInput.vue', () => ({
    default: { props: ['modelValue'], template: '<input :value="modelValue" />' },
}))

vi.mock('@/components/DeleteConfirmDialog.vue', () => ({
    default: { template: '<div class="delete-dialog-stub" />' },
}))

vi.mock('@/components/ErrorState.vue', () => ({
    default: { template: '<div class="error-state-stub" />' },
}))

// Stub MaskEntryTable to expose each entry as a div keyed by entry id.
// This lets tests assert which entries land inside which epoch card without
// coupling to MaskEntryTable's own rendering logic.
vi.mock('@/components/MaskEntryTable.vue', () => ({
    default: defineComponent({
        name: 'MaskEntryTable',
        props: {
            entries: { type: Array, default: () => [] },
            canWrite: Boolean,
            saving: Boolean,
        },
        template: `
            <div class="mask-entry-table-stub">
                <div
                    v-for="e in entries"
                    :key="e.id"
                    class="entry-row"
                    :data-entry-id="String(e.id)"
                />
            </div>
        `,
    }),
}))

import { makeAuthMock } from './helpers/mockUseAuth'
import { useAuth } from '@/composables/useAuth'
import { getMaskLog } from '@/api/equipment'
import MaskLogManager from '@/components/MaskLogManager.vue'
import { Select } from '@/components/ui/select'
import DatePickerInput from '@/components/DatePickerInput.vue'
import type { MaskEpochResponse, MaskLogEntryResponse } from '@/types'

// --- Fixtures ---

function makeEntry(overrides: Partial<MaskLogEntryResponse> = {}): MaskLogEntryResponse {
    return {
        id: 1,
        brand: 'ResMed',
        model: 'AirFit P10',
        style: 'pillows',
        start_date: null,
        size: null,
        notes: null,
        ...overrides,
    }
}

function makeEpoch(overrides: Partial<MaskEpochResponse> = {}): MaskEpochResponse {
    return {
        mask_type: 'PillowMask',
        style: 'pillows',
        start_date: '2025-01-01',
        end_date: '2025-06-30',
        days_count: 180,
        device_id: 1,
        device_name: 'AirSense 11',
        ...overrides,
    }
}

// --- Helpers ---

function setupAuthMock() {
    vi.mocked(useAuth).mockReturnValue(makeAuthMock() as never)
}

async function mountManager(epochs: MaskEpochResponse[] = []) {
    const wrapper = mount(MaskLogManager, { props: { epochs } })
    await flushPromises()
    return wrapper
}

// --- Tests ---

describe('MaskLogManager', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        setupAuthMock()
    })

    it('test_entry_within_epoch_range_renders_inside_epoch_card', async () => {
        const epoch = makeEpoch({ start_date: '2025-01-01', end_date: '2025-06-30' })
        const entry = makeEntry({ id: 10, start_date: '2025-03-15' })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch])

        const cards = wrapper.findAll('.epoch-card')
        expect(cards).toHaveLength(1)
        expect(cards[0].find('[data-entry-id="10"]').exists()).toBe(true)
    })

    it('test_entry_with_null_start_date_renders_under_other_entries_heading', async () => {
        const epoch = makeEpoch({ start_date: '2025-01-01', end_date: '2025-06-30' })
        const entry = makeEntry({ id: 20, start_date: null })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch])

        // Entry is not inside any epoch card.
        const cards = wrapper.findAll('.epoch-card')
        expect(cards.every((c) => !c.find('[data-entry-id="20"]').exists())).toBe(true)
        // "Other entries" heading is shown because epochs exist but the entry
        // has no date and therefore cannot be bucketed into any epoch.
        expect(wrapper.find('.other-entries-heading').exists()).toBe(true)
        // The entry is still rendered (in the other-entries table).
        expect(wrapper.find('[data-entry-id="20"]').exists()).toBe(true)
    })

    it('test_dated_entry_outside_all_epoch_ranges_renders_under_other_entries_heading', async () => {
        const epoch = makeEpoch({ start_date: '2025-01-01', end_date: '2025-06-30' })
        const entry = makeEntry({ id: 30, start_date: '2026-01-01' })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch])

        // Entry falls after the epoch's end_date — no match.
        const cards = wrapper.findAll('.epoch-card')
        expect(cards.every((c) => !c.find('[data-entry-id="30"]').exists())).toBe(true)
        expect(wrapper.find('.other-entries-heading').exists()).toBe(true)
        expect(wrapper.find('[data-entry-id="30"]').exists()).toBe(true)
    })

    it('test_overlapping_epochs_entry_appears_only_in_first_epoch_in_array_order', async () => {
        const epoch1 = makeEpoch({
            start_date: '2025-01-01',
            end_date: '2025-06-30',
            device_id: 1,
            device_name: 'Device A',
        })
        const epoch2 = makeEpoch({
            start_date: '2025-03-01',
            end_date: '2025-09-30',
            device_id: 2,
            device_name: 'Device B',
        })
        // 2025-04-15 falls inside both epoch ranges.
        const entry = makeEntry({ id: 40, start_date: '2025-04-15' })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch1, epoch2])

        const cards = wrapper.findAll('.epoch-card')
        expect(cards).toHaveLength(2)
        // Entry lands in the FIRST matching epoch (epoch1).
        expect(cards[0].find('[data-entry-id="40"]').exists()).toBe(true)
        // Entry does NOT duplicate into epoch2.
        expect(cards[1].find('[data-entry-id="40"]').exists()).toBe(false)
        // No "Other entries" heading since the entry was bucketed into an epoch.
        expect(wrapper.find('.other-entries-heading').exists()).toBe(false)
    })

    it('test_zero_epochs_renders_no_epoch_cards_and_no_other_entries_heading', async () => {
        const entry = makeEntry({ id: 50, start_date: '2025-03-15' })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([])

        // No epoch structure at all.
        expect(wrapper.findAll('.epoch-card')).toHaveLength(0)
        // Heading only appears when epochs exist; with zero epochs it must be absent.
        expect(wrapper.find('.other-entries-heading').exists()).toBe(false)
        // Entry is still displayed in the plain (non-epoch) table.
        expect(wrapper.find('[data-entry-id="50"]').exists()).toBe(true)
    })
})

describe('MaskLogManager epoch prefill', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        setupAuthMock()
    })

    it('test_add_details_prefills_brand_model_size_from_matching_style_entry', async () => {
        // Existing entry with full_face equipment details and entry-specific notes/date.
        const entry = makeEntry({
            id: 1,
            brand: 'ResMed',
            model: 'AirFit F20',
            style: 'full_face',
            size: 'M',
            start_date: '2025-02-01',
            notes: 'fits well',
        })
        // Epoch with matching style but a different start date (new trial period).
        const epoch = makeEpoch({
            style: 'full_face',
            mask_type: 'FullFaceMask',
            start_date: '2025-07-01',
            end_date: '2025-12-31',
        })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch])

        await wrapper.find('.epoch-card button').trigger('click')
        await nextTick()

        // Brand and model come from the existing same-style entry (catalog mode, 4 Selects rendered).
        const selects = wrapper.findAllComponents(Select)
        expect(selects[0].props('modelValue')).toBe('ResMed')
        expect(selects[1].props('modelValue')).toBe('AirFit F20')
        // selects[2] is the style Select — value comes from the epoch
        expect(selects[3].props('modelValue')).toBe('M')

        // Start date must be the epoch's start_date, not the entry's.
        const datePicker = wrapper.findComponent(DatePickerInput)
        expect(datePicker.find('input').element.value).toBe('2025-07-01')

        // Notes are entry-specific and must not carry over.
        expect((wrapper.find('#mask-notes').element as HTMLInputElement).value).toBe('')
    })

    it('test_add_details_ignores_entries_of_different_style', async () => {
        // Only a pillows entry exists — should not template a nasal epoch.
        const entry = makeEntry({
            id: 1,
            brand: 'ResMed',
            model: 'AirFit P10',
            style: 'pillows',
            size: 'S',
            start_date: '2025-02-01',
        })
        const epoch = makeEpoch({
            style: 'nasal',
            mask_type: 'NasalMask',
            start_date: '2025-07-01',
            end_date: '2025-12-31',
        })
        vi.mocked(getMaskLog).mockResolvedValue([entry])

        const wrapper = await mountManager([epoch])

        await wrapper.find('.epoch-card button').trigger('click')
        await nextTick()

        // No matching template — brand field stays blank.
        // Without a brand, the Model Select is also suppressed (3 Selects: Brand, Style, Size).
        const selects = wrapper.findAllComponents(Select)
        expect(selects).toHaveLength(3)
        expect(selects[0].props('modelValue')).toBeUndefined()
        // Notes remain empty.
        expect((wrapper.find('#mask-notes').element as HTMLInputElement).value).toBe('')
    })

    it('test_add_details_uses_later_start_date_entry_when_multiple_same_style_exist', async () => {
        // Two pillows entries; the later one (id=2, date=2025-06-01) should win.
        const older = makeEntry({
            id: 1,
            brand: 'ResMed',
            model: 'AirFit P10',
            style: 'pillows',
            size: 'S',
            start_date: '2025-03-01',
        })
        const newer = makeEntry({
            id: 2,
            brand: 'Philips Respironics',
            model: 'Nuance',
            style: 'pillows',
            size: 'M',
            start_date: '2025-06-01',
        })
        // API returns oldest-first; reversed in the component = [newer, older].
        const epoch = makeEpoch({
            style: 'pillows',
            mask_type: 'PillowMask',
            start_date: '2025-09-01',
            end_date: '2025-12-31',
        })
        vi.mocked(getMaskLog).mockResolvedValue([older, newer])

        const wrapper = await mountManager([epoch])

        await wrapper.find('.epoch-card button').trigger('click')
        await nextTick()

        // The newer entry's brand wins (not ResMed from the older one).
        const selects = wrapper.findAllComponents(Select)
        expect(selects[0].props('modelValue')).toBe('Philips Respironics')
        expect(selects[1].props('modelValue')).toBe('Nuance')
        expect(selects[3].props('modelValue')).toBe('M')
    })
})
