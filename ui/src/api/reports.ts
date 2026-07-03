import api from './client'

export async function getSummaryReport(fromDate: string, toDate: string): Promise<Blob> {
    const { data } = await api.get<Blob>('/reports/summary', {
        params: { from_date: fromDate, to_date: toDate },
        responseType: 'blob',
    })
    return data
}

export async function getComparisonReport(
    fromA: string,
    toA: string,
    fromB: string,
    toB: string,
): Promise<Blob> {
    const { data } = await api.get<Blob>('/reports/comparison', {
        params: { from_a: fromA, to_a: toA, from_b: fromB, to_b: toB },
        responseType: 'blob',
    })
    return data
}
