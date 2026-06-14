export function ahiClass(ahi: number | null | undefined): string {
    if (ahi == null) return ''
    if (ahi < 5) return 'ahi-good'
    if (ahi < 15) return 'ahi-mild'
    return 'ahi-severe'
}
