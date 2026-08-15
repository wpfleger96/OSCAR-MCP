import { describe, expect, it } from 'vitest'
import { categorizeSettings, settingLabel, SETTING_CATEGORIES } from '@/utils/deviceSettings'
import { GLOSSARY } from '@/utils/glossary'

const allKeys = SETTING_CATEGORIES.flatMap((c) => c.keys)

describe('device setting glossary integration', () => {
    it('every setting key has a matching glossary entry', () => {
        for (const key of allKeys) {
            const entry = GLOSSARY[`setting_${key}`]
            expect(entry, `missing glossary entry for setting_${key}`).toBeDefined()
        }
    })

    it('every glossary label matches the setting label', () => {
        for (const key of allKeys) {
            expect(GLOSSARY[`setting_${key}`].label).toBe(settingLabel(key))
        }
    })
})

describe('categorizeSettings glossary keys', () => {
    it('attaches the setting_ glossary key to a known categorized entry', () => {
        const { categories } = categorizeSettings({ ipap: '14' })
        const ipap = categories.flatMap((c) => c.entries).find((e) => e.key === 'ipap')

        expect(ipap?.glossaryKey).toBe('setting_ipap')
    })

    it('leaves unknown keys in other with no glossary key', () => {
        const { other } = categorizeSettings({ ipap: '14', some_unknown_key: 'x' })
        const unknown = other.find((e) => e.key === 'some_unknown_key')

        expect(unknown).toBeDefined()
        expect(unknown?.glossaryKey).toBeUndefined()
    })
})
