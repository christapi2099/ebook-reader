import { test, expect } from '@playwright/test'
import { MOCK_SENTENCES, makeMinimalPdf, MOCK_AUDIO_CHUNK } from './fixtures/mock-data'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

test.describe('Buffering States — MediaBar, TextViewer, AudioProgressBar', () => {
  let driver: WsDriver

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)

    await page.route('**/*', async route => {
      const url = route.request().url()
      if (url.includes('/documents/') && url.includes('/sentences')) {
        await route.fulfill({ json: MOCK_SENTENCES })
      } else if (url.includes('/library/') && url.includes('/progress')) {
        await route.fulfill({ json: { sentence_index: 0 } })
      } else if (url.includes('/library/') && !url.includes('/progress')) {
        await route.fulfill({
          json: { id: 'buffer-test', title: 'Buffer Test', author: 'Test', file_type: 'pdf', page_count: 5 }
        })
      } else if (url.includes('/uploads/')) {
        await route.fulfill({ status: 200, headers: { 'content-type': 'application/pdf' }, body: makeMinimalPdf() })
      } else if (url.includes('/user/settings')) {
        await route.fulfill({ json: { last_book_id: 'buffer-test', last_sentence_index: 0, highlight_enabled: true } })
      } else {
        await route.continue()
      }
    })

    await driver.install(page, 'buffer-test')
    await page.goto('/reader/buffer-test')
  })

  test('MediaBar spinner shows data-loading=true after play click', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Play' })
    await expect(playBtn).toBeVisible()
    await playBtn.click()

    const pauseBtn = page.getByRole('button', { name: 'Pause' })
    await expect(pauseBtn).toHaveAttribute('data-loading', 'true')
  })

  test('MediaBar spinner data-loading clears after buffer filled', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Play' })
    await playBtn.click()

    await driver.sendSentenceStart(0, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceStart(1, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceStart(2, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(200)

    const pauseBtn = page.getByRole('button', { name: 'Pause' })
    await expect(pauseBtn).toHaveAttribute('data-loading', 'false')
  })

  test('TextViewer sentence has animate-pulse when buffering', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Play' })
    await playBtn.click()

    const currentSentence = page.locator('[data-sentence-index="0"]')
    await expect(currentSentence).toHaveClass(/animate-pulse/)
  })

  test('AudioProgressBar shimmer visible when buffering', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Play' })
    await playBtn.click()

    const shimmer = page.locator('.shimmer-shine')
    await expect(shimmer).toBeVisible()
  })

  test('All indicators clear together after buffer filled', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Play' })
    await playBtn.click()

    // Fill buffer
    await driver.sendSentenceStart(0, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceStart(1, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceStart(2, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(200)

    // Check all indicators cleared
    const pauseBtn = page.getByRole('button', { name: 'Pause' })
    await expect(pauseBtn).toHaveAttribute('data-loading', 'false')

    const currentSentence = page.locator('[data-sentence-index="0"]')
    const classes = await currentSentence.getAttribute('class')
    expect(classes?.includes('animate-pulse')).toBe(false)

    const shimmer = page.locator('.shimmer-shine')
    await expect(shimmer).not.toBeVisible()
  })
})
