import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { MOCK_SENTENCES, makeMinimalPdf, MOCK_AUDIO_CHUNK } from './fixtures/mock-data'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

const speedGroup = (page: Page) => page.getByRole('group', { name: 'Playback speed' })

test.describe('MediaBar Controls', () => {
  let driver: WsDriver

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)
    await page.route('**/documents/*/sentences', r => r.fulfill({ json: MOCK_SENTENCES }))
    await page.route('**/library/*/progress', r => r.fulfill({ json: { sentence_index: 0 } }))
    await page.route('**/uploads/**', r => r.fulfill({
      status: 200,
      headers: { 'content-type': 'application/pdf' },
      body: makeMinimalPdf(),
    }))
    await driver.install(page, 'book-1')
    await page.goto('/reader/book-1')
  })

  test('play button toggles to pause', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()
    await page.getByRole('button', { name: 'Play' }).click()
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  })

  test('pause button toggles back to play', async ({ page }) => {
    await page.getByRole('button', { name: 'Play' }).click()
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(page.getByRole('button', { name: 'Play' })).toBeVisible()
  })

  test('speed change highlights selected pill', async ({ page }) => {
    const group = speedGroup(page)
    await group.getByRole('button', { name: '1x' }).click()
    await expect(group.getByRole('button', { name: '1x' })).toHaveAttribute('aria-pressed', 'true')
    await group.getByRole('button', { name: '2x' }).click()
    await expect(group.getByRole('button', { name: '2x' })).toHaveAttribute('aria-pressed', 'true')
    await expect(group.getByRole('button', { name: '1x' })).toHaveAttribute('aria-pressed', 'false')
  })

  test('rewind sends seek with correct index', async ({ page }) => {
    await page.getByRole('button', { name: 'Play' }).click()
    await driver.sendSentenceStart(5, 1)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(200)
    await page.getByRole('button', { name: 'Rewind 5 sentences' }).click()
    const seekActions = driver.actionsOf('seek')
    expect(seekActions.length).toBeGreaterThan(0)
    expect(seekActions.at(-1)?.['to_index']).toBe(0)
  })

  test('speed change while paused does not send WS action', async ({ page }) => {
    await page.getByRole('button', { name: 'Play' }).click()
    await page.getByRole('button', { name: 'Pause' }).click()
    await speedGroup(page).getByRole('button', { name: '2x' }).click()
    await page.clock.runFor(200)
    const playActions = driver.actionsOf('play')
    expect(playActions.filter(a => a['speed'] === 2.0)).toHaveLength(0)
  })
})
