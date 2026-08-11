import { test, type Route } from '@playwright/test'
import {
    summaryFixture,
    trendsFixture,
    daysFixture,
    sessionsFixture,
    devicesFixture,
    deviceDetailFixture,
    periodsFixture,
    recordsFixture,
    rxHistoryFixture,
    rxCurrentFixture,
    rxCompareFixture,
    rxChangesFixture,
    maskLogFixture,
    maskEpochsFixture,
    sessionDetailFixture,
} from './tests/fixtures/api-fixtures'

function routeApi(route: Route) {
    const url = route.request().url()

    if (url.includes('/stats/records')) return route.fulfill({ json: recordsFixture })
    if (url.includes('/stats/summary')) return route.fulfill({ json: summaryFixture })
    if (url.includes('/stats/trends')) return route.fulfill({ json: trendsFixture })
    if (url.includes('/stats/periods')) return route.fulfill({ json: periodsFixture })
    if (url.includes('/equipment/masks/epochs')) return route.fulfill({ json: maskEpochsFixture })
    if (url.includes('/equipment/masks')) return route.fulfill({ json: maskLogFixture })
    if (url.includes('/rx/changes')) return route.fulfill({ json: rxChangesFixture })
    if (url.includes('/rx/compare')) return route.fulfill({ json: rxCompareFixture })
    if (url.includes('/rx/current')) return route.fulfill({ json: rxCurrentFixture })
    if (url.includes('/rx/history')) return route.fulfill({ json: rxHistoryFixture })
    if (url.includes('/devices/1')) return route.fulfill({ json: deviceDetailFixture })
    if (url.includes('/devices')) return route.fulfill({ json: devicesFixture })
    if (url.includes('/sessions/1470/events')) return route.fulfill({ json: [] })
    if (url.includes('/sessions/1470/waveforms'))
        return route.fulfill({
            json: {
                timestamps: [],
                values: [],
                sample_rate: 25,
                unit: 'L/min',
                total_samples: 0,
                downsampled: false,
                returned_samples: 0,
            },
        })
    if (url.includes('/sessions/1470')) return route.fulfill({ json: sessionDetailFixture })
    if (url.includes('/days')) return route.fulfill({ json: daysFixture })
    if (url.includes('/sessions')) return route.fulfill({ json: sessionsFixture })

    return route.fulfill({ json: {} })
}

test.beforeEach(async ({ page }) => {
    await page.route('/api/v1/**', routeApi)
})

test('dashboard', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('.summary-row')
    await page.waitForTimeout(800)
    await page.screenshot({ path: 'screenshots/dashboard.png' })
})

test('sessions', async ({ page }) => {
    await page.goto('/sessions')
    await page.waitForSelector('.sessions-table tbody tr')
    await page.screenshot({ path: 'screenshots/sessions.png' })
})

test('stats', async ({ page }) => {
    await page.goto('/stats')
    await page.waitForSelector('.records-grid')
    await page.waitForSelector('.trend-chart canvas')
    await page.waitForTimeout(800)
    await page.screenshot({ path: 'screenshots/stats.png' })
})

test('stats error state', async ({ page }) => {
    await page.route('**/api/v1/stats/**', (route) =>
        route.fulfill({
            status: 500,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Internal Server Error' }),
        }),
    )
    await page.goto('/stats')
    await page.waitForSelector('.error-state')
    await page.waitForTimeout(800)
    await page.screenshot({ path: 'screenshots/stats-error.png' })
})

test('rx-history', async ({ page }) => {
    await page.goto('/rx')
    await page.waitForSelector('.setting-pill')
    await page.screenshot({ path: 'screenshots/rx-history.png' })
})

test('session-detail', async ({ page }) => {
    await page.goto('/sessions/1470')
    await page.waitForSelector('.session-detail .stats-section')
    await page.screenshot({ path: 'screenshots/session-detail.png' })
})

test('reports', async ({ page }) => {
    await page.goto('/reports')
    await page.waitForSelector('.reports-view')
    await page.getByText('Comparison').click()
    await page.waitForSelector('.rx-select')
    await page.screenshot({ path: 'screenshots/reports.png' })
})

test('dashboard dark', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('.summary-row')
    await page.waitForTimeout(800)
    await page.getByText('Dark Mode').click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'screenshots/dashboard-dark.png' })
})

test('session-detail dark', async ({ page }) => {
    await page.goto('/sessions/1470')
    await page.waitForSelector('.session-detail .stats-section')
    await page.getByText('Dark Mode').click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'screenshots/session-detail-dark.png' })
})

test('equipment', async ({ page }) => {
    await page.goto('/equipment')
    await page.waitForSelector('.equipment-view .device-card')
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'screenshots/equipment.png' })
})

test('equipment dark', async ({ page }) => {
    await page.goto('/equipment')
    await page.waitForSelector('.equipment-view .device-card')
    await page.getByText('Dark Mode').click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'screenshots/equipment-dark.png' })
})
