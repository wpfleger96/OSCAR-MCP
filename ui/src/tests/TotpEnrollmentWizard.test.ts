import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/totp')
vi.mock('@/components/ui/button', () => ({
    Button: { template: '<button type="submit"><slot /></button>' },
}))

import TotpEnrollmentWizard from '@/components/TotpEnrollmentWizard.vue'
import { setupTotp, confirmTotp } from '@/api/totp'

const SETUP_RESPONSE = {
    secret: 'JBSWY3DPEHPK3PXP',
    otpauth_uri: 'otpauth://totp/SNORE:user@example.com?secret=JBSWY3DPEHPK3PXP',
    qr_data_uri: 'data:image/svg+xml;base64,PHN2Zy8+',
}

const CONFIRM_RESPONSE = {
    recovery_codes: [
        'code-01',
        'code-02',
        'code-03',
        'code-04',
        'code-05',
        'code-06',
        'code-07',
        'code-08',
        'code-09',
        'code-10',
    ],
}

describe('TotpEnrollmentWizard — step 1 (QR + secret)', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(setupTotp).mockResolvedValue(SETUP_RESPONSE)
    })

    it('test_renders_qr_image_and_secret_after_setup', async () => {
        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()

        const img = wrapper.find('img[alt="TOTP QR code"]')
        expect(img.exists()).toBe(true)
        expect(img.attributes('src')).toBe(SETUP_RESPONSE.qr_data_uri)

        expect(wrapper.text()).toContain(SETUP_RESPONSE.secret)
    })

    it('test_setup_error_shows_error_message', async () => {
        vi.mocked(setupTotp).mockRejectedValueOnce(new Error('Already enabled'))

        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()

        const alert = wrapper.find('[role="alert"]')
        expect(alert.exists()).toBe(true)
        expect(alert.text()).toContain('Already enabled')
    })

    it('test_continue_button_advances_to_step_2', async () => {
        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()

        const continueBtn = wrapper.findAll('button').find((b) => b.text().includes('Continue'))
        expect(continueBtn).toBeTruthy()
        await continueBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Step 2 should have the code input
        expect(wrapper.find('input[inputmode="numeric"]').exists()).toBe(true)
    })
})

describe('TotpEnrollmentWizard — step 2 (verify code)', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(setupTotp).mockResolvedValue(SETUP_RESPONSE)
    })

    async function mountAtStep2() {
        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()
        const continueBtn = wrapper.findAll('button').find((b) => b.text().includes('Continue'))
        await continueBtn!.trigger('click')
        await wrapper.vm.$nextTick()
        return wrapper
    }

    it('test_confirm_success_advances_to_step_3_and_shows_recovery_codes', async () => {
        vi.mocked(confirmTotp).mockResolvedValueOnce(CONFIRM_RESPONSE)

        const wrapper = await mountAtStep2()
        await wrapper.find('input[inputmode="numeric"]').setValue('123456')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        expect(confirmTotp).toHaveBeenCalledWith({ code: '123456' })
        // Recovery codes should now be visible
        for (const code of CONFIRM_RESPONSE.recovery_codes) {
            expect(wrapper.text()).toContain(code)
        }
    })

    it('test_confirm_error_shows_inline_error_stays_on_step_2', async () => {
        vi.mocked(confirmTotp).mockRejectedValueOnce(new Error('Invalid code'))

        const wrapper = await mountAtStep2()
        await wrapper.find('input[inputmode="numeric"]').setValue('999999')
        await wrapper.find('form').trigger('submit')
        await flushPromises()

        const alert = wrapper.find('[role="alert"]')
        expect(alert.exists()).toBe(true)
        expect(alert.text()).toContain('Invalid code')
        // Still on step 2 — code input still present
        expect(wrapper.find('input[inputmode="numeric"]').exists()).toBe(true)
    })

    it('test_back_button_returns_to_step_1', async () => {
        const wrapper = await mountAtStep2()
        const backBtn = wrapper.findAll('button').find((b) => b.text().includes('Back'))
        expect(backBtn).toBeTruthy()
        await backBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Back on step 1 — QR image should be visible
        expect(wrapper.find('img[alt="TOTP QR code"]').exists()).toBe(true)
    })
})

describe('TotpEnrollmentWizard — step 3 (recovery codes)', () => {
    beforeEach(() => {
        vi.resetAllMocks()
        vi.mocked(setupTotp).mockResolvedValue(SETUP_RESPONSE)
        vi.mocked(confirmTotp).mockResolvedValue(CONFIRM_RESPONSE)
    })

    async function mountAtStep3() {
        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()
        const continueBtn = wrapper.findAll('button').find((b) => b.text().includes('Continue'))
        await continueBtn!.trigger('click')
        await wrapper.vm.$nextTick()
        await wrapper.find('input[inputmode="numeric"]').setValue('123456')
        await wrapper.find('form').trigger('submit')
        await flushPromises()
        return wrapper
    }

    it('test_finish_button_disabled_until_acknowledged', async () => {
        const wrapper = await mountAtStep3()

        const finishBtn = wrapper.findAll('button').find((b) => b.text().includes('Finish'))
        expect(finishBtn).toBeTruthy()
        expect((finishBtn!.element as HTMLButtonElement).disabled).toBe(true)

        // Check the acknowledgment checkbox
        await wrapper.find('input[type="checkbox"]').setValue(true)
        await wrapper.vm.$nextTick()
        expect((finishBtn!.element as HTMLButtonElement).disabled).toBe(false)
    })

    it('test_finish_button_emits_done_event', async () => {
        const wrapper = await mountAtStep3()

        await wrapper.find('input[type="checkbox"]').setValue(true)
        await wrapper.vm.$nextTick()

        const finishBtn = wrapper.findAll('button').find((b) => b.text().includes('Finish'))
        await finishBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        expect(wrapper.emitted('done')).toBeTruthy()
        expect(wrapper.emitted('done')!.length).toBe(1)
    })
})
