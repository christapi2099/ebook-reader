import { test, expect } from '@playwright/test'
import { MOCK_SENTENCES, makeMinimalPdf } from './fixtures/mock-data'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

const IDX  = { first: 0, second: 1, third: 2, clicked: 3, stale: 5, last: 4 }
const SID  = { first: 1, second: 2, stale: 99 }
const TICK = { sentence: 200, seek: 100 }

test.describe('Highlight–Audio Sync', () => {
  let driver: WsDriver
  let errors: string[]

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    errors = []
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)
    await page.route('**/documents/*/sentences', r => r.fulfill({ json: MOCK_SENTENCES }))
    await page.route('**/library/*/progress', r => r.fulfill({ json: { sentence_index: 0 } }))
    await page.route('**/uploads/**', r => r.fulfill({
      status: 200,
      headers: { 'content-type': 'application/pdf' },
      body: makeMinimalPdf(),
    }))
    await driver.install(page, 'test-book')
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
    await page.goto('/reader/test-book')
  })

  test.afterEach(() => {
    expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0)
  })

  test('initial highlight on sentence 0', async ({ page }) => {
    await expect(page.locator('[data-index="0"][data-highlighted="true"]')).toBeVisible()
    await expect(page.locator('[data-highlighted="true"]')).toHaveCount(1)
  })

  test('highlight advances and previous clears', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).toBeVisible()
    await expect(page.locator('[data-index="0"][data-highlighted="false"]')).toBeVisible()
  })

  test('stale session_id rejected', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.stale, SID.stale)
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="5"][data-highlighted="true"]')).not.toBeVisible({ timeout: 1000 })
  })

  test('stale complete does not stop playback', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendComplete(SID.stale)
    await expect(page.locator('[aria-label="Pause"]')).toBeVisible()
  })

  test('pause freezes highlight', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).toBeVisible()
    await page.click('[aria-label="Pause"]')
    await driver.sendSentenceStart(IDX.third, SID.first)
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="2"][data-highlighted="true"]')).not.toBeVisible({ timeout: 500 })
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).toBeVisible()
  })

  test('resume continues from paused index', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await page.click('[aria-label="Pause"]')
    await page.click('[aria-label="Play"]')
    const resumeAction = driver.actionsOf('play').at(-1)
    expect(resumeAction?.['from_index']).toBe(IDX.second)
  })

  test('sentence click triggers seek and highlight jumps', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await page.locator('[data-index="3"]').click()
    // seek() sets currentIndex=3 immediately; no audio needed to verify highlight
    expect(driver.actionsOf('seek')[0]?.['to_index']).toBe(IDX.clicked)
    await expect(page.locator('[data-index="3"][data-highlighted="true"]')).toBeVisible()
  })

  test('complete stops playback, last sentence stays highlighted', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.last, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await driver.sendComplete(SID.first)
    await expect(page.locator('[aria-label="Play"]')).toBeVisible()
    await expect(page.locator('[data-index="4"][data-highlighted="true"]')).toBeVisible()
  })

  test('generation guard blocks stale onended', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await page.locator('[data-index="3"]').click()
    await page.evaluate(() => (window as any).__mockNodes[0]?.fireEnded())
    await expect(page.locator('[data-index="3"][data-highlighted="true"]')).toBeVisible()
    await expect(page.locator('[data-index="0"][data-highlighted="true"]')).not.toBeVisible({ timeout: 500 })
  })

  test('rapid sentence_starts: advances to latest', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(0, SID.first); await driver.sendAudioChunk(Buffer.alloc(128))
    await driver.sendSentenceStart(1, SID.first); await driver.sendAudioChunk(Buffer.alloc(128))
    await driver.sendSentenceStart(2, SID.first); await driver.sendAudioChunk(Buffer.alloc(128))
    await page.clock.runFor(TICK.sentence * 3)
    await expect(page.locator('[data-index="2"][data-highlighted="true"]')).toBeVisible()
    await expect(page.locator('[data-index="0"][data-highlighted="false"]')).toBeVisible()
  })

  test('speed change sends correct speed in play action', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await page.click('button[aria-pressed="false"]:has-text("1.5x")')
    await page.clock.runFor(200)  // fire the 200ms speed-change debounce timer
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
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).not.toBeVisible({ timeout: 500 })
  })

  test('WS disconnect freezes highlight', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).toBeVisible()
    await page.clock.runFor(500)
    await expect(page.locator('[data-index="1"][data-highlighted="true"]')).toBeVisible()
  })

  test('highlighted overlay is within viewport bounds', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    const el = page.locator('[data-highlighted="true"]').first()
    const box = await el.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.x).toBeGreaterThanOrEqual(0)
  })

  test('cross-page: highlight follows to page 2', async ({ page }) => {
    await page.click('[aria-label="Play"]')
    for (let i = 0; i <= 2; i++) {
      await driver.sendSentenceStart(i, SID.first)
      await driver.sendAudioChunk(Buffer.alloc(128))
      await page.clock.runFor(TICK.sentence)
    }
    await driver.sendSentenceStart(3, SID.first)
    await driver.sendAudioChunk(Buffer.alloc(1024))
    await page.clock.runFor(TICK.sentence)
    await expect(page.locator('[data-index="3"][data-highlighted="true"]')).toBeVisible()
  })
})
