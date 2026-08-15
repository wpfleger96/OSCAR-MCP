import { defineConfig } from '@playwright/test'

export default defineConfig({
    testMatch: 'screenshot.spec.ts',
    use: {
        baseURL: 'http://localhost:4173',
    },
    projects: [
        {
            name: 'desktop',
            use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } },
        },
        {
            name: 'mobile',
            use: {
                browserName: 'chromium',
                viewport: { width: 390, height: 844 },
                hasTouch: true,
                isMobile: true,
            },
        },
    ],
    webServer: {
        command: 'pnpm run preview',
        url: 'http://localhost:4173',
        reuseExistingServer: !process.env.CI,
        stdout: 'pipe',
        stderr: 'pipe',
    },
})
