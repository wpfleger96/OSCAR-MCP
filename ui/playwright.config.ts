import { defineConfig } from '@playwright/test'

export default defineConfig({
    testMatch: 'screenshot.spec.ts',
    use: {
        baseURL: 'http://localhost:4173',
        viewport: { width: 1440, height: 900 },
    },
    projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
    webServer: {
        command: 'npm run preview',
        url: 'http://localhost:4173',
        reuseExistingServer: !process.env.CI,
        stdout: 'pipe',
        stderr: 'pipe',
    },
})
