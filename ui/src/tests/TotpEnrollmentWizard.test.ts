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

    it('test_setup_error_shows_retry_button_and_retries_on_success', async () => {
        vi.mocked(setupTotp)
            .mockRejectedValueOnce(new Error('Server error'))
            .mockResolvedValueOnce(SETUP_RESPONSE)

        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()

        // Error state with retry button
        expect(wrapper.find('[role="alert"]').text()).toContain('Server error')
        const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('Try again'))
        expect(retryBtn).toBeTruthy()

        // Click retry — second call resolves with setup data
        await retryBtn!.trigger('click')
        await flushPromises()

        expect(setupTotp).toHaveBeenCalledTimes(2)
        expect(wrapper.find('img[alt="TOTP QR code"]').exists()).toBe(true)
        expect(wrapper.find('[role="alert"]').exists()).toBe(false)
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

    it('test_copy_secret_failure_shows_fallback_message', async () => {
        Object.defineProperty(navigator, 'clipboard', {
            value: { writeText: vi.fn().mockRejectedValueOnce(new Error('Not allowed')) },
            writable: true,
            configurable: true,
        })

        const wrapper = mount(TotpEnrollmentWizard)
        await flushPromises()

        const copyBtn = wrapper.findAll('button').find((b) => b.text() === 'Copy')
        expect(copyBtn).toBeTruthy()
        await copyBtn!.trigger('click')
        await flushPromises()

        expect(wrapper.text()).toContain('Copy failed')
        // The fallback message is in a role="alert" element
        const alerts = wrapper.findAll('[role="alert"]')
        const fallbackAlert = alerts.find((a) => a.text().includes('Copy failed'))
        expect(fallbackAlert).toBeTruthy()
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

    it('test_back_button_clears_verify_code_and_error', async () => {
        vi.mocked(confirmTotp).mockRejectedValueOnce(new Error('Invalid code'))

        const wrapper = await mountAtStep2()

        // Enter code and trigger a verify error
        await wrapper.find('input[inputmode="numeric"]').setValue('999999')
        await wrapper.find('form').trigger('submit')
        await flushPromises()
        expect(wrapper.find('[role="alert"]').text()).toContain('Invalid code')

        // Click back
        const backBtn = wrapper.findAll('button').find((b) => b.text().includes('Back'))
        await backBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Back on step 1
        expect(wrapper.find('img[alt="TOTP QR code"]').exists()).toBe(true)

        // Advance to step 2 again
        const continueBtn = wrapper.findAll('button').find((b) => b.text().includes('Continue'))
        await continueBtn!.trigger('click')
        await wrapper.vm.$nextTick()

        // Code input is empty and no error shown
        expect((wrapper.find('input[inputmode="numeric"]').element as HTMLInputElement).value).toBe(
            '',
        )
        expect(wrapper.find('[role="alert"]').exists()).toBe(false)
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

    it('test_copy_all_failure_shows_fallback_message', async () => {
        Object.defineProperty(navigator, 'clipboard', {
            value: { writeText: vi.fn().mockRejectedValueOnce(new Error('Not allowed')) },
            writable: true,
            configurable: true,
        })

        const wrapper = await mountAtStep3()

        const copyAllBtn = wrapper.findAll('button').find((b) => b.text().includes('Copy all'))
        expect(copyAllBtn).toBeTruthy()
        await copyAllBtn!.trigger('click')
        await flushPromises()

        const alerts = wrapper.findAll('[role="alert"]')
        const fallbackAlert = alerts.find((a) => a.text().includes('Copy failed'))
        expect(fallbackAlert).toBeTruthy()
    })
})
