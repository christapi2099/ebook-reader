import { test, expect, type Page } from '@playwright/test'
import { WsDriver } from './fixtures/ws-driver'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'

test.describe('Text Reader Bug Fixes', () => {
  let driver: WsDriver

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    await driver.install(page, 'text-book-1')
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)
    await page.route('**/library', r => r.fulfill({ json: [] }))
    await page.route('**/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/')
  })

  async function setupReadingMode(page: Page) {
    await page.route('**/documents/text', r => r.fulfill({
      json: { book_id: 'text-book-1', sentence_count: 3, already_existed: false }
    }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'The quick brown fox jumps over the lazy dog.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'She sells seashells by the seashore.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 2, text: 'Peter Piper picked a peck of pickled peppers.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())
    await page.getByPlaceholder('Paste or type your text here...').fill('Some text to read aloud')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.locator('[data-sentence-index="0"]')).toBeVisible({ timeout: 5000 })
  }

  test('BUG1: Read Aloud should NOT auto-play — Play button should be visible', async ({ page }) => {
    await setupReadingMode(page)

    // After transitioning to reading mode, the Play button should be visible
    // (NOT the Pause button, because audio should NOT have started playing)
    await expect(page.getByRole('button', { name: 'Play' })).toBeVisible({ timeout: 3000 })

    // No WS play action should have been sent yet
    const playActions = driver.actionsOf('play')
    expect(playActions).toHaveLength(0)
  })

  test('BUG1: Clicking Play after Read Aloud starts audio', async ({ page }) => {
    await setupReadingMode(page)

    // User manually clicks play
    await page.getByRole('button', { name: 'Play' }).click()

    // Now the Pause button should be visible (audio is playing)
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()

    // A WS play action should have been sent
    const playActions = driver.actionsOf('play')
    expect(playActions.length).toBeGreaterThanOrEqual(1)
  })

  test('BUG2: Word highlighting should not run before Play is pressed', async ({ page }) => {
    await setupReadingMode(page)

    // Before pressing play, the first sentence should be highlighted at the sentence level
    // but there should be NO word-by-word highlighting (no bold/dark words)
    const sentence0 = page.locator('[data-sentence-index="0"]')
    await expect(sentence0).toBeVisible()

    // The sentence should show plain text, NOT word-by-word spans
    // If word-by-word highlighting ran without audio, there would be styled spans
    const html = await sentence0.innerHTML()
    // Should NOT contain the word-level highlighting style (rgba background)
    expect(html).not.toContain('font-weight: 600')
  })

  test('BUG3: Pause should stop playback and show Play button', async ({ page }) => {
    await setupReadingMode(page)

    // Click play
    await page.getByRole('button', { name: 'Play' }).click()
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()

    // Click pause
    await page.getByRole('button', { name: 'Pause' }).click()

    // Play button should reappear
    await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()
  })

  test('BUG3: Pause-then-Play resumes from same position', async ({ page }) => {
    await setupReadingMode(page)

    // Click play
    await page.getByRole('button', { name: 'Play' }).click()
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()

    // Click pause
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()

    // Click play again
    await page.getByRole('button', { name: 'Play' }).click()

    // Should show pause button (playing state)
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()

    // Should have sent at least 2 play actions (initial + resume), last one with from_index = 0
    await page.waitForTimeout(100)
    const playActions = driver.actionsOf('play')
    expect(playActions.length).toBeGreaterThanOrEqual(2)
    const resumeAction = playActions.at(-1)
    expect(resumeAction?.['from_index']).toBe(0)
  })

  test('BUG4: Rapid play-pause-play should not get stuck', async ({ page }) => {
    await setupReadingMode(page)

    // Rapid play-pause-play cycle
    await page.getByRole('button', { name: 'Play' }).click()
    await page.getByRole('button', { name: 'Pause' }).click()
    await page.getByRole('button', { name: 'Play' }).click()

    // Final state should be playing (Pause button visible)
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible({ timeout: 2000 })
  })
})
