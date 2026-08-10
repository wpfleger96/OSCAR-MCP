export interface AhiScaleEntry {
    label: string
    color: string
    maxAhi: number | null // null = no upper bound (catch-all)
}

// Shared AHI colour scale used by CalendarHeatmap and the DashboardView legend.
// Thresholds: <5 good, 5–9 mild, 10–14 moderate, ≥15 severe.
// Note: this display scale is stricter than the common clinical convention
// (<5 normal, 5–15 mild, 15–30 moderate, >30 severe).
export const AHI_COLOR_SCALE: AhiScaleEntry[] = [
    { label: 'AHI < 5 — Good', color: '#22c55e', maxAhi: 5 },
    { label: 'AHI 5–9 — Mild', color: '#eab308', maxAhi: 10 },
    { label: 'AHI 10–14 — Moderate', color: '#f97316', maxAhi: 15 },
    { label: 'AHI ≥ 15 — Severe', color: '#ef4444', maxAhi: null },
]

export function ahiColorClass(ahi: number | null): string {
    if (ahi == null) return 'cell--empty'
    if (ahi < 5) return 'cell--good'
    if (ahi < 10) return 'cell--mild'
    if (ahi < 15) return 'cell--moderate'
    return 'cell--severe'
}
