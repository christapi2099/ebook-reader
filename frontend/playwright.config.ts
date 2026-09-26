import { defineConfig } from '@playwright/test'

// Only the critical user flows run by default, tagged `@critical` in the specs.
// See tests/README.md for the list and the reasoning: e2e is the slowest gate and
// the suite had grown to 121 tests, most of them guarding behaviour that unit
// tests already cover (bionic rendering, search styling, buffering indicators).
// Set E2E_ALL=1 to run everything.
const CRITICAL_TAG = /@critical/
const grep = process.env.E2E_ALL ? undefined : CRITICAL_TAG

export default defineConfig({
  grep,
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report' }]],
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
  },
})
