import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { MOCK_SENTENCES, makeMinimalPdf, MOCK_AUDIO_CHUNK, MOCK_AUDIO_CHUNK_SMALL } from './fixtures/mock-data'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

const IDX  = { first: 0, second: 1, third: 2, clicked: 3, stale: 5, last: 4 }
const SID  = { first: 1, second: 2, stale: 99 }
const TICK = { sentence: 200, seek: 100 }

const playBtn = (page: Page) => page.getByRole('button', { name: 'Play' })
const pauseBtn = (page: Page) => page.getByRole('button', { name: 'Pause' })
const speedGroup = (page: Page) => page.getByRole('group', { name: 'Playback speed' })
const sentenceOverlay = (page: Page, index: number, highlighted: string) =>
  page.locator(`[data-index="${index}"][data-highlighted="${highlighted}"]`)

test.describe('Highlight–Audio Sync', () => {
  let driver: WsDriver
  let errors: string[]

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    errors = []
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)

    // Mock all API endpoints
    await page.route('**/*', async route => {
      const url = route.request().url()
      if (url.includes('/documents/') && url.includes('/sentences')) {
        await route.fulfill({ json: MOCK_SENTENCES })
      } else if (url.includes('/library/') && url.includes('/progress')) {
        await route.fulfill({ json: { sentence_index: 0 } })
      } else if (url.includes('/library/') && !url.includes('/progress')) {
        await route.fulfill({
          json: { id: 'test-book', title: 'Test Book', author: 'Test Author', file_type: 'PDF', page_count: 5 }
        })
      } else if (url.includes('/uploads/')) {
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'application/pdf' },
          body: makeMinimalPdf(),
        })
      } else {
        await route.continue()
      }
    })

    await driver.install(page, 'test-book')
    page.on('console', m => { if (m.type() === 'error') {
      // Filter out common non-error messages
      if (!m.text().includes('favicon') && !m.text().includes('Failed to load resource')) {
        errors.push(m.text())
      }
    }})
    await page.goto('/reader/test-book')
  })

  test.afterEach(() => {
    expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0)
  })

  test('initial highlight on sentence 0', async ({ page }) => {
    await expect(sentenceOverlay(page, 0, 'true')).toBeVisible()
    await expect(page.locator('[data-highlighted="true"]')).toHaveCount(1)
  })

  test('highlight advances and previous clears', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 1, 'true')).toBeVisible()
    await expect(sentenceOverlay(page, 0, 'false')).toBeVisible()
  })

  test('stale session_id rejected', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.stale, SID.stale)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, IDX.stale, 'true')).not.toBeVisible({ timeout: 300 })
  })

  test('stale complete does not stop playback', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendComplete(SID.stale)
    await expect(pauseBtn(page)).toBeVisible()
  })

  test('pause freezes highlight', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 1, 'true')).toBeVisible()
    await pauseBtn(page).click()
    await driver.sendSentenceStart(IDX.third, SID.first)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 2, 'true')).not.toBeVisible({ timeout: 300 })
    await expect(sentenceOverlay(page, 1, 'true')).toBeVisible()
  })

  test('resume continues from paused index', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await pauseBtn(page).click()
    await playBtn(page).click()
    const resumeAction = driver.actionsOf('play').at(-1)
    expect(resumeAction?.['from_index']).toBe(IDX.second)
  })

  test('sentence click triggers seek and highlight jumps', async ({ page }) => {
    await playBtn(page).click()
    await page.locator('[data-index="3"]').click()
    expect(driver.actionsOf('seek')[0]?.['to_index']).toBe(IDX.clicked)
    await expect(sentenceOverlay(page, 3, 'true')).toBeVisible()
  })

  test('complete stops playback, last sentence stays highlighted', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.last, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await driver.sendComplete(SID.first)
    await expect(playBtn(page)).toBeVisible()
    await expect(sentenceOverlay(page, 4, 'true')).toBeVisible()
  })

  test('generation guard blocks stale onended', async ({ page }) => {
    await playBtn(page).click()
    await page.locator('[data-index="3"]').click()
    await page.evaluate(() => (window as any).__mockNodes[0]?.fireEnded())
    await expect(sentenceOverlay(page, 3, 'true')).toBeVisible()
    await expect(sentenceOverlay(page, 0, 'true')).not.toBeVisible({ timeout: 300 })
  })

  test('rapid sentence_starts: advances to latest', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(0, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK_SMALL)
    await driver.sendSentenceStart(1, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK_SMALL)
    await driver.sendSentenceStart(2, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK_SMALL)
    await page.clock.runFor(TICK.sentence * 3)
    await expect(sentenceOverlay(page, 2, 'true')).toBeVisible()
    await expect(sentenceOverlay(page, 0, 'false')).toBeVisible()
  })

  test('speed change sends correct speed in play action', async ({ page }) => {
    await playBtn(page).click()
    await speedGroup(page).getByRole('button', { name: '1.5x' }).click()
    await page.clock.runFor(200)
    const speedAction = driver.actionsOf('play').at(-1)
    expect(speedAction?.['speed']).toBe(1.5)
  })

  test('decodeAudioData rejection does not advance highlight', async ({ page }) => {
    await page.evaluate(() => {
      const OrigAC = (window as any).AudioContext
      ;(window as any).AudioContext = class extends OrigAC {
        decodeAudioData() { return Promise.reject(new Error('corrupt')) }
      }
    })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 1, 'true')).not.toBeVisible({ timeout: 300 })
  })

  test('data stream stall freezes highlight', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 1, 'true')).toBeVisible()
    await page.clock.runFor(500)
    await expect(sentenceOverlay(page, 1, 'true')).toBeVisible()
  })

  test('highlighted overlay is within viewport bounds', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    const el = page.locator('[data-highlighted="true"]').first()
    const box = await el.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.x).toBeGreaterThanOrEqual(0)
  })

  test('cross-page: highlight follows to page 2', async ({ page }) => {
    await playBtn(page).click()
    for (let i = 0; i <= 2; i++) {
      await driver.sendSentenceStart(i, SID.first)
      await driver.sendAudioChunk(MOCK_AUDIO_CHUNK_SMALL)
      await page.clock.runFor(TICK.sentence)
    }
    await driver.sendSentenceStart(3, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 3, 'true')).toBeVisible()
  })

  test('rapid play-pause-play resets state cleanly', async ({ page }) => {
    await playBtn(page).click()
    await driver.sendSentenceStart(0, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await pauseBtn(page).click()
    await playBtn(page).click()
    await driver.sendSentenceStart(0, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await expect(sentenceOverlay(page, 0, 'true')).toBeVisible()
    await expect(pauseBtn(page)).toBeVisible()
  })

  test('speed change debounce prevents double play action', async ({ page }) => {
    await playBtn(page).click()
    const afterInitialPlay = driver.actionsOf('play').length
    await speedGroup(page).getByRole('button', { name: '1.5x' }).click()
    await page.clock.runFor(50)
    await speedGroup(page).getByRole('button', { name: '2x' }).click()
    await page.clock.runFor(200)
    const newPlayActions = driver.actionsOf('play').slice(afterInitialPlay)
    expect(newPlayActions.length).toBe(1)
    expect(newPlayActions[0]?.['speed']).toBe(2.0)
  })
})
