/**
 * Static CPAP mask catalog powering prefilled dropdowns.
 *
 * Catalog compiled from manufacturer/retailer data, 2018–2026 era.
 * Classification notes:
 *   - Hybrid and total-face masks are classified full_face.
 *   - Bleep's adhesive interfaces are classified pillows.
 *   - "For Her" variants are folded into their base models.
 *   - The form's Custom option covers anything missing from this catalog.
 */

// Keep MaskStyle values in sync with: src/snore/api/schemas.py (MaskStyle enum),
// DB CHECK constraints in migrations 008/009, and
// src/snore/services/mask_epoch_service.py (normalization map).
export type MaskStyle = 'pillows' | 'nasal' | 'full_face'

export interface MaskModel {
    name: string
    style: MaskStyle
}

export interface MaskBrand {
    name: string
    models: MaskModel[]
}

export const CUSTOM_VALUE = '__custom__'

export const MASK_CATALOG: MaskBrand[] = [
    {
        name: 'ResMed',
        models: [
            { name: 'AirFit N20', style: 'nasal' },
            { name: 'AirFit N30', style: 'nasal' },
            { name: 'AirFit N30i', style: 'nasal' },
            { name: 'AirFit N10', style: 'nasal' },
            { name: 'AirFit F20', style: 'full_face' },
            { name: 'AirFit F30', style: 'full_face' },
            { name: 'AirFit F30i', style: 'full_face' },
            { name: 'AirFit F40', style: 'full_face' },
            { name: 'AirFit F10', style: 'full_face' },
            { name: 'AirFit P10', style: 'pillows' },
            { name: 'AirFit P30i', style: 'pillows' },
            { name: 'AirTouch N20', style: 'nasal' },
            { name: 'AirTouch N30i', style: 'nasal' },
            { name: 'AirTouch F20', style: 'full_face' },
            { name: 'AirTouch F30i', style: 'full_face' },
            { name: 'Mirage FX', style: 'nasal' },
            { name: 'Mirage Quattro', style: 'full_face' },
            { name: 'Quattro Air', style: 'full_face' },
            { name: 'Quattro FX', style: 'full_face' },
            { name: 'Swift FX', style: 'pillows' },
            { name: 'Swift LT', style: 'pillows' },
        ],
    },
    {
        name: 'Philips Respironics',
        models: [
            { name: 'DreamWear Nasal', style: 'nasal' },
            { name: 'DreamWisp', style: 'nasal' },
            { name: 'Wisp', style: 'nasal' },
            { name: 'Pico', style: 'nasal' },
            { name: 'ComfortGel Blue Nasal', style: 'nasal' },
            { name: 'TrueBlue', style: 'nasal' },
            { name: 'DreamWear Full Face', style: 'full_face' },
            { name: 'Amara', style: 'full_face' },
            { name: 'Amara Gel', style: 'full_face' },
            { name: 'Amara View', style: 'full_face' },
            { name: 'ComfortGel Blue Full', style: 'full_face' },
            { name: 'FitLife', style: 'full_face' },
            { name: 'DreamWear Silicone Pillows', style: 'pillows' },
            { name: 'Nuance', style: 'pillows' },
            { name: 'Nuance Pro', style: 'pillows' },
        ],
    },
    {
        name: 'Fisher & Paykel',
        models: [
            { name: 'Evora Nasal', style: 'nasal' },
            { name: 'Nova Nasal', style: 'nasal' },
            { name: 'Eson 2', style: 'nasal' },
            { name: 'Eson', style: 'nasal' },
            { name: 'Zest Q', style: 'nasal' },
            { name: 'Solo Nasal', style: 'nasal' },
            { name: 'Evora Full', style: 'full_face' },
            { name: 'Vitera', style: 'full_face' },
            { name: 'Simplus', style: 'full_face' },
            { name: 'Forma', style: 'full_face' },
            { name: 'Solo Pillows', style: 'pillows' },
            { name: 'Nova Micro', style: 'pillows' },
            { name: 'Brevida', style: 'pillows' },
            { name: 'Opus 360', style: 'pillows' },
            { name: 'Pilairo Q', style: 'pillows' },
        ],
    },
    {
        name: 'Apex Medical',
        models: [
            { name: 'Wizard 210', style: 'nasal' },
            { name: 'Wizard 310', style: 'nasal' },
            { name: 'Wizard 220', style: 'full_face' },
            { name: 'Wizard 320', style: 'full_face' },
            { name: 'Wizard 230', style: 'pillows' },
        ],
    },
    {
        name: 'Bleep',
        models: [
            { name: 'DreamPort', style: 'pillows' },
            { name: 'Eclipse', style: 'pillows' },
        ],
    },
    {
        name: 'BMC Medical',
        models: [
            { name: 'iVolve N2', style: 'nasal' },
            { name: 'iVolve F1A', style: 'full_face' },
            { name: 'iVolve F5A', style: 'full_face' },
        ],
    },
    {
        name: 'Circadiance',
        models: [
            { name: 'SleepWeaver Advance', style: 'nasal' },
            { name: 'SleepWeaver Élan', style: 'nasal' },
            { name: 'SleepWeaver 3D', style: 'nasal' },
            { name: 'SleepWeaver Anew', style: 'full_face' },
        ],
    },
    {
        name: 'Drive DeVilbiss',
        models: [
            { name: 'NasalFit Deluxe EZ', style: 'nasal' },
            { name: 'ComfortFit Deluxe', style: 'full_face' },
        ],
    },
    {
        name: 'Hans Rudolph',
        models: [{ name: 'V2 Full Face', style: 'full_face' }],
    },
    {
        name: 'Löwenstein',
        models: [
            { name: 'JOYCEone Nasal', style: 'nasal' },
            { name: 'CARA Nasal', style: 'nasal' },
            { name: 'JOYCEone Full Face', style: 'full_face' },
            { name: 'CARA Full Face', style: 'full_face' },
        ],
    },
    {
        name: 'React Health (3B Medical)',
        models: [
            { name: 'Siesta Nasal', style: 'nasal' },
            { name: 'Siesta Full Face', style: 'full_face' },
        ],
    },
    {
        name: 'Sleepnet',
        models: [
            { name: 'Ascend Nasal', style: 'nasal' },
            { name: 'Aura', style: 'nasal' },
            { name: 'iQ Blue', style: 'nasal' },
            { name: 'Phantom 2', style: 'nasal' },
            { name: 'Ascend Full Face', style: 'full_face' },
            { name: 'Mojo 2', style: 'full_face' },
        ],
    },
]

export const SIZES_BY_STYLE: Record<MaskStyle, string[]> = {
    pillows: ['XS', 'S', 'M', 'L'],
    nasal: ['Petite', 'S', 'M', 'L', 'Wide'],
    full_face: ['XS', 'S', 'M', 'L'],
}

export function findBrand(name: string): MaskBrand | undefined {
    return MASK_CATALOG.find((b) => b.name === name)
}

export function findModel(brandName: string, modelName: string): MaskModel | undefined {
    return findBrand(brandName)?.models.find((m) => m.name === modelName)
}

// UI display order: nasal → full_face → pillows.
export const STYLE_OPTIONS: { value: MaskStyle; label: string }[] = [
    { value: 'nasal', label: 'Nasal' },
    { value: 'full_face', label: 'Full Face' },
    { value: 'pillows', label: 'Pillows' },
]

export function styleLabel(style: string | null | undefined): string {
    if (!style) return ''
    return STYLE_OPTIONS.find((o) => o.value === style)?.label ?? style
}

export function maskEntryName(entry: {
    brand?: string | null
    model?: string | null
    style?: string | null
}): string {
    const parts = [entry.brand, entry.model].filter(Boolean)
    if (parts.length) return parts.join(' ')
    return styleLabel(entry.style) || 'unspecified mask'
}
