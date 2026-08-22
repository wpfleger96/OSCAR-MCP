/** Client-side file downloads with CSV formula-injection escaping.
 *
 * Centralizes the escaping ValidationView previously inlined so every validation
 * panel neutralizes the same injection vectors identically. */

export function downloadBlob(content: string, filename: string, mimeType: string): void {
    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
}

export function downloadJson(value: unknown, filename: string): void {
    downloadBlob(JSON.stringify(value, null, 2), filename, 'application/json')
}

/** Quote one CSV cell, neutralizing spreadsheet formula injection. */
export function csvCell(value: unknown): string {
    const s = String(value ?? '')
    // Neutralize formula injection: prefix cells starting with =, +, -, or @
    const safe = /^[=+\-@]/.test(s) ? `'${s}` : s
    // Wrap in double quotes, escape embedded double quotes as ""
    return `"${safe.replaceAll('"', '""')}"`
}

/** Assemble a CSV string from a header row and body rows (all cells escaped). */
export function buildCsv(headers: string[], rows: unknown[][]): string {
    return [headers, ...rows].map((r) => r.map(csvCell).join(',')).join('\n')
}

export function downloadCsv(headers: string[], rows: unknown[][], filename: string): void {
    downloadBlob(buildCsv(headers, rows), filename, 'text/csv')
}
