import { describe, expect, it } from 'vitest'
import {
    categorizeSettings,
    formatSettingValue,
    settingLabel,
    SETTING_CATEGORIES,
} from '@/utils/deviceSettings'
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

describe('categorizeSettings', () => {
    it('attaches the setting_ glossary key to a known categorized entry', () => {
        const groups = categorizeSettings({ ipap: '14' })
        const ipap = groups.flatMap((g) => g.entries).find((e) => e.key === 'ipap')

        expect(ipap?.glossaryKey).toBe('setting_ipap')
    })

    it('places unknown keys in an Other settings group with no glossary key', () => {
        const groups = categorizeSettings({ ipap: '14', some_unknown_key: 'x' })
        const other = groups.find((g) => g.label === 'Other settings')
        const unknown = other?.entries.find((e) => e.key === 'some_unknown_key')

        expect(unknown).toBeDefined()
        expect(unknown?.glossaryKey).toBeUndefined()
    })

    it('returns an empty array for an empty settings dict', () => {
        expect(categorizeSettings({})).toEqual([])
    })
})

describe('formatSettingValue', () => {
    const cases: [name: string, key: string, raw: string, expected: string][] = [
        ['boolean true → On', 'ramp_enabled', 'true', 'On'],
        ['boolean 1 → On', 'ramp_enabled', '1', 'On'],
        ['boolean false → Off', 'ramp_enabled', 'false', 'Off'],
        ['pressure formatted to one decimal with unit', 'ipap', '14', '14.0 cmH₂O'],
        ['pressure non-numeric passthrough', 'ipap', 'auto', 'auto'],
        ['ramp_time appends min', 'ramp_time', '20', '20 min'],
        ['tube_temp converts Celsius to Fahrenheit', 'tube_temp', '30', '86.0°F'],
        ['tube_temp non-numeric passthrough', 'tube_temp', 'n/a', 'n/a'],
        ['humidity_level 0 → Off', 'humidity_level', '0', 'Off'],
        ['humidity_level non-zero passthrough', 'humidity_level', '3', '3'],
        ['unknown key passthrough', 'some_unknown_key', 'raw', 'raw'],
    ]

    it.each(cases)('%s', (_name, key, raw, expected) => {
        expect(formatSettingValue(key, raw)).toBe(expected)
    })
})
