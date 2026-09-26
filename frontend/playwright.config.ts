import { defineConfig } from '@playwright/test'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

// Only the critical user flows run by default, tagged `@critical` in the specs.
// See tests/README.md for the list and the reasoning: e2e is the slowest gate and
// the suite had grown to 121 tests, most of them guarding behaviour that unit
// tests already cover (bionic rendering, search styling, buffering indicators).
// Set E2E_ALL=1 to run everything.
const CRITICAL_TAG = /@critical/
const grep = process.env.E2E_ALL ? undefined : CRITICAL_TAG

/**
 * Find a Chromium this machine actually has.
 *
 * Playwright 1.59.1 pins chromium revision 1217. That build is absent here and
 * `npx playwright install chromium` cannot fetch it (it was killed at 420 s having
 * downloaded nothing), while revisions 1234 and 1243 are already in the cache — so
 * without this the e2e gate cannot run at all, which is worse than running it on a
 * slightly newer Chromium.
 *
 * The tradeoff is explicit: this picks the newest available build rather than the
 * pinned one, so it is not the exact browser Playwright was released against.
 * `PLAYWRIGHT_CHROMIUM_PATH` overrides the search when that matters, and if no
 * build is found the config falls back to Playwright's own resolution so a machine
 * with the pinned build installed behaves exactly as before.
 */
function findChromium(): string | undefined {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH

  const cache = join(homedir(), '.cache', 'ms-playwright')
  if (!existsSync(cache)) return undefined

  const builds = readdirSync(cache)
    .map((dir) => /^chromium-(\d+)$/.exec(dir))
    .filter((match): match is RegExpExecArray => match !== null)
    .map((match) => ({ dir: match[0], revision: Number(match[1]) }))
    .sort((a, b) => b.revision - a.revision)

  for (const build of builds) {
    // Newer builds ship chrome-linux64; older ones chrome-linux.
    for (const layout of ['chrome-linux64', 'chrome-linux']) {
      const exe = join(cache, build.dir, layout, 'chrome')
      if (existsSync(exe)) return exe
    }
  }
  return undefined
}

const chromiumPath = findChromium()

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
    launchOptions: chromiumPath ? { executablePath: chromiumPath } : {},
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
