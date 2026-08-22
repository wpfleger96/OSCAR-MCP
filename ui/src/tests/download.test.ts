// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { csvCell, buildCsv } from '@/utils/download'

describe('csvCell formula-injection guard', () => {
    it('test_formula_text_is_prefixed_with_apostrophe', () => {
        expect(csvCell('=SUM(A1:A2)')).toBe('"\'=SUM(A1:A2)"')
        expect(csvCell('+cmd')).toBe('"\'+cmd"')
        expect(csvCell('@ref')).toBe('"\'@ref"')
        expect(csvCell('-alpha')).toBe('"\'-alpha"')
    })

    it('test_tab_and_cr_led_text_is_prefixed', () => {
        expect(csvCell('\t=evil')).toBe('"\'\t=evil"')
        expect(csvCell('\r@evil')).toBe('"\'\r@evil"')
    })

    it('test_negative_numbers_export_as_numbers_not_text', () => {
        // Regression: legitimate negative metrics (rho, biases) must not become '-0.5000.
        expect(csvCell('-0.5000')).toBe('"-0.5000"')
        expect(csvCell('-42')).toBe('"-42"')
        expect(csvCell(-0.5)).toBe('"-0.5"')
    })

    it('test_positive_numbers_and_plain_text_untouched', () => {
        expect(csvCell('0.42')).toBe('"0.42"')
        expect(csvCell('hello')).toBe('"hello"')
    })

    it('test_lone_dash_or_operator_still_neutralized', () => {
        // Not a finite number, so still a formula-injection vector.
        expect(csvCell('-')).toBe('"\'-"')
    })

    it('test_embedded_quotes_are_doubled', () => {
        expect(csvCell('a"b')).toBe('"a""b"')
    })

    it('test_buildcsv_joins_escaped_rows', () => {
        const csv = buildCsv(['a', 'b'], [['-0.5', '=x']])
        expect(csv).toBe('"a","b"\n"-0.5","\'=x"')
    })
})
