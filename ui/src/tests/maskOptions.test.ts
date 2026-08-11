import { describe, expect, it } from 'vitest'
import { styleLabel, maskEntryName } from '@/utils/maskOptions'

describe('styleLabel', () => {
    it('test_null_returns_empty_string', () => {
        expect(styleLabel(null)).toBe('')
    })

    it('test_undefined_returns_empty_string', () => {
        expect(styleLabel(undefined)).toBe('')
    })

    it('test_empty_string_returns_empty_string', () => {
        expect(styleLabel('')).toBe('')
    })

    it('test_nasal_maps_to_Nasal', () => {
        expect(styleLabel('nasal')).toBe('Nasal')
    })

    it('test_full_face_maps_to_Full_Face', () => {
        expect(styleLabel('full_face')).toBe('Full Face')
    })

    it('test_pillows_maps_to_Pillows', () => {
        expect(styleLabel('pillows')).toBe('Pillows')
    })

    it('test_unknown_value_returned_verbatim', () => {
        expect(styleLabel('swift_fx')).toBe('swift_fx')
    })
})

describe('maskEntryName', () => {
    it('test_brand_and_model_joined_with_space', () => {
        expect(maskEntryName({ brand: 'ResMed', model: 'AirFit P10' })).toBe('ResMed AirFit P10')
    })

    it('test_brand_only_returns_brand', () => {
        expect(maskEntryName({ brand: 'ResMed', model: null })).toBe('ResMed')
    })

    it('test_model_only_returns_model', () => {
        expect(maskEntryName({ brand: null, model: 'AirFit P10' })).toBe('AirFit P10')
    })

    it('test_no_name_but_known_style_falls_back_to_styleLabel', () => {
        expect(maskEntryName({ brand: null, model: null, style: 'nasal' })).toBe('Nasal')
    })

    it('test_nothing_returns_unspecified_mask', () => {
        expect(maskEntryName({ brand: null, model: null, style: null })).toBe('unspecified mask')
    })

    it('test_empty_strings_treated_as_absent_and_returns_unspecified_mask', () => {
        expect(maskEntryName({ brand: '', model: '', style: '' })).toBe('unspecified mask')
    })
})
