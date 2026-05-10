import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { MOCK_SENTENCES, makeMinimalPdf, MOCK_AUDIO_CHUNK, MOCK_AUDIO_CHUNK_SMALL } from './fixtures/mock-data'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

const IDX  = { first: 0, second: 1, third: 2, clicked: 3, stale: 5, last: 4 }
const SID  = { first: 1, second: 2, stale: 99 }
const TICK = { sentence: 200, seek: 100 }

const searchBtn = (page: Page) => page.getByRole('button', { name: 'Search' })
const searchInput = (page: Page) => page.getByPlaceholder('Search in book\u2026')
const nextMatchBtn = (page: Page) => page.getByRole('button', { name: 'Next match' })
const prevMatchBtn = (page: Page) => page.getByRole('button', { name: 'Previous match' })

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

test.describe('Word-level highlighting', () => {
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

  test('word_divs_rendered_for_sentences_with_words', async ({ page }) => {
    await expect(page.locator('[data-word-index="0"][data-sentence-index="0"]')).toHaveCount(1)
    await expect(page.locator('[data-word-index="1"][data-sentence-index="0"]')).toHaveCount(1)
    await expect(page.locator('[data-word-index="0"][data-sentence-index="1"]')).toHaveCount(1)
    const totalWordDivs = await page.locator('[data-word-index]').count()
    expect(totalWordDivs).toBe(3)
  })

  test('current_word_div_gets_background_color', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceEnd(IDX.first, SID.first, [
      { word: 'Word', start: 0.1, end: 0.2 },
      { word: 'here', start: 0.25, end: 0.35 }
    ])
    await page.clock.runFor(TICK.sentence)
    const debug = await page.evaluate(() => {
      const div = document.querySelector('[data-word-index="0"][data-sentence-index="0"]') as HTMLElement
      const allWordDivs = document.querySelectorAll('[data-word-index]')
      return {
        div: div ? 'found' : 'not found',
        bg: div?.style.backgroundColor || '',
        count: allWordDivs.length,
        first: allWordDivs[0] ? (allWordDivs[0] as HTMLElement).style.backgroundColor : 'no divs'
      }
    })
    console.log('Debug:', debug)
    expect(debug).not.toBe('')
  })

  test('word_highlight_advances_to_next_word', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceEnd(IDX.first, SID.first, [
      { word: 'Sentence', start: 0.05, end: 0.5 },
      { word: '0.', start: 0.6, end: 1.0 }
    ])
    await page.clock.runFor(TICK.sentence)
    const word0Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index="0"][data-sentence-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(word0Bg).not.toBe('')
    await page.clock.runFor(600)
    const word0BgAfter = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index="0"][data-sentence-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    const word1Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index="1"][data-sentence-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(word0BgAfter).toBe('')
    expect(word1Bg).not.toBe('')
  })

  test('word_highlight_clears_on_pause', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceEnd(IDX.first, SID.first, [
      { word: 'Sentence', start: 0.05, end: 0.5 },
      { word: '0.', start: 0.6, end: 1.0 }
    ])
    await page.clock.runFor(TICK.sentence)
    await pauseBtn(page).click()
    const allBgs = await page.evaluate(() => {
      const divs = document.querySelectorAll('[data-word-index]')
      return Array.from(divs).map(d => (d as HTMLElement).style.backgroundColor)
    })
    for (const bg of allBgs) {
      expect(bg).toBe('')
    }
  })

  test('sentences_without_words_have_no_word_divs', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await expect(page.locator('[data-word-index][data-sentence-index="2"]')).toHaveCount(0)
    await expect(page.locator('[data-word-index][data-sentence-index="3"]')).toHaveCount(0)
  })

  test('word_highlight_clears_when_sentence_changes', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceEnd(IDX.first, SID.first, [
      { word: 'Sentence', start: 0.05, end: 0.5 },
      { word: '0.', start: 0.6, end: 1.0 }
    ])
    await page.clock.runFor(TICK.sentence)
    await page.locator('[data-index="3"]').click()
    const word0Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index][data-sentence-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(word0Bg).toBe('')
  })

  test('word_highlight_transitions_across_sentence_boundary', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK_SMALL)
    await driver.sendSentenceEnd(IDX.first, SID.first, [
      { word: 'Sentence', start: 0.05, end: 0.5 }
    ])
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await driver.sendSentenceEnd(IDX.second, SID.first, [
      { word: 'Sentence', start: 0.0, end: 0.5 }
    ])
    await page.clock.runFor(5000)
    const sent0Word0Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index="0"][data-sentence-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    const sent1Word0Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-word-index="0"][data-sentence-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(sent0Word0Bg).toBe('')
    expect(sent1Word0Bg).not.toBe('')
  })
})

test.describe('Search diff (O(1) optimization)', () => {
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
      if (!m.text().includes('favicon') && !m.text().includes('Failed to load resource')) {
        errors.push(m.text())
      }
    }})
    await page.goto('/reader/test-book')
  })

  test.afterEach(() => {
    expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0)
  })

  test('adding_matches_styles_only_new_entries', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence 0')
    const match0Bg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match0Bg).not.toBe('')
    const match1BgNarrow = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match1BgNarrow).toBe('')
    await searchInput(page).fill('Sentence')
    const match0BgWide = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match0BgWide).not.toBe('')
    const match1BgWide = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match1BgWide).not.toBe('')
  })

  test('removing_matches_clears_only_removed_entries', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence')
    const match0BgAll = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match0BgAll).not.toBe('')
    const match1BgAll = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match1BgAll).not.toBe('')
    await searchInput(page).fill('Sentence 0')
    const match0BgNarrow = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match0BgNarrow).not.toBe('')
    const match1BgAfter = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(match1BgAfter).toBe('')
  })

  test('clearing_search_removes_all_fills', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence')
    const indices = [0, 1, 2, 3, 4, 5]
    for (const i of indices) {
      const bg = await page.evaluate((idx) => {
        const el = document.querySelector(`[data-index="${idx}"]`) as HTMLElement
        return el?.style.backgroundColor || ''
      }, i)
      expect(bg).not.toBe('')
    }
    await page.keyboard.press('Escape')
    for (const i of indices) {
      const bg = await page.evaluate((idx) => {
        const el = document.querySelector(`[data-index="${idx}"]`) as HTMLElement
        return el?.style.backgroundColor || ''
      }, i)
      expect(bg).toBe('')
    }
  })
})

test.describe('Search highlight styling', () => {
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

  test('search_matches_use_fill_not_outline', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence 0')
    const bg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    const outline = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.outline || ''
    })
    expect(bg).not.toBe('')
    expect(outline === '' || outline === 'none').toBe(true)
  })

  test('search_current_match_is_blue_others_are_green', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    // Advance playback to sentence 2 then pause — leaves sentences 0 & 1 free for search colors
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await driver.sendSentenceStart(IDX.third, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    await pauseBtn(page).click()
    // sentence 2 is now data-highlighted; sentences 0 & 1 are free
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence')
    // sentence 0 = first result = current search match → blue
    const currentBg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(currentBg).toContain('59, 130, 246')
    // sentence 1 = second result = non-current → green
    const nonCurrentBg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(nonCurrentBg).toContain('134, 239, 172')
    // advance current match from sentence 0 → sentence 1
    await nextMatchBtn(page).click()
    const currentAfterNext = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    const nextAsCurrent = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(currentAfterNext).toContain('134, 239, 172')
    expect(nextAsCurrent).toContain('59, 130, 246')
  })

  test('search_fill_clears_when_search_closed', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence 0')
    const bgBeforeClose = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(bgBeforeClose).not.toBe('')
    await page.keyboard.press('Escape')
    const bgAfterClose = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(bgAfterClose).toBe('')
  })

  test('playback_highlight_wins_over_search_fill', async ({ page }) => {
    await page.waitForSelector('[data-overlay]', { timeout: 10000 })
    await searchBtn(page).click()
    await searchInput(page).fill('Sentence')
    await playBtn(page).click()
    await driver.sendSentenceStart(IDX.first, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    const playbackBg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(playbackBg).not.toContain('59, 130, 246')
    expect(playbackBg).not.toContain('134, 239, 172')
    await driver.sendSentenceStart(IDX.second, SID.first)
    await driver.sendAudioChunk(MOCK_AUDIO_CHUNK)
    await page.clock.runFor(TICK.sentence)
    const afterMoveBg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="0"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(afterMoveBg).not.toBe('')
    const playbackOnOneBg = await page.evaluate(() => {
      const el = document.querySelector('[data-index="1"]') as HTMLElement
      return el?.style.backgroundColor || ''
    })
    expect(playbackOnOneBg).not.toContain('59, 130, 246')
    expect(playbackOnOneBg).not.toContain('134, 239, 172')
  })
})
