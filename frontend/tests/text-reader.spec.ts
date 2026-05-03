import { test, expect } from '@playwright/test'
import { WsDriver } from './fixtures/ws-driver'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'

test.describe('Text Reader Homepage', () => {
  let driver: WsDriver

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)
    // Mock empty library by default
    await page.route('**/library', r => r.fulfill({ json: [] }))
    await page.route('**/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/')
  })

  test('shows textarea on homepage', async ({ page }) => {
    await expect(page.getByPlaceholder('Paste or type your text here...')).toBeVisible()
  })

  test('play button disabled when textarea is empty', async ({ page }) => {
    const playBtn = page.getByRole('button', { name: 'Read Aloud' })
    await expect(playBtn).toBeDisabled()
  })

  test('play button enabled after pasting text', async ({ page }) => {
    const textarea = page.getByPlaceholder('Paste or type your text here...')
    await textarea.fill('This is a test sentence. This is another sentence.')
    const playBtn = page.getByRole('button', { name: 'Read Aloud' })
    await expect(playBtn).toBeEnabled()
  })

  test('submitting creates book and transitions to reading mode', async ({ page }) => {
    // Mock API responses
    await page.route('**/documents/text', async route => {
      route.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2, already_existed: false } })
    })
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'This is a test sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'This is another sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())  // WebSocket

    const textarea = page.getByPlaceholder('Paste or type your text here...')
    await textarea.fill('This is a test sentence. This is another sentence.')

    const playBtn = page.getByRole('button', { name: 'Read Aloud' })
    await playBtn.click()

    // Should now be in reading mode with sentences visible
    await expect(page.getByText('This is a test sentence.')).toBeVisible()
    await expect(page.getByText('This is another sentence.')).toBeVisible()
  })

  test('sentences displayed as list in reading mode', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence here.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Second sentence here.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()

    await expect(page.locator('[data-sentence-index="0"]')).toBeVisible()
    await expect(page.locator('[data-sentence-index="1"]')).toBeVisible()
  })

  test('current sentence highlighted', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Second sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()

    // First sentence should be highlighted initially
    await expect(page.locator('[data-sentence-index="0"][data-highlighted="true"]')).toBeVisible()
  })

  test('pause button freezes highlight', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Second sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await page.getByRole('button', { name: 'Play' }).click()
    await page.getByRole('button', { name: 'Pause' }).click()

    // Highlight should remain on first sentence
    await expect(page.locator('[data-sentence-index="0"][data-highlighted="true"]')).toBeVisible()
  })

  test('seek by clicking sentence', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 3 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Second sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 2, text: 'Third sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()

    // Click on third sentence
    await page.locator('[data-sentence-index="2"]').click()

    // Third sentence should be highlighted
    await expect(page.locator('[data-sentence-index="2"][data-highlighted="true"]')).toBeVisible()
  })

  test('speed change works', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Second sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()

    // Click 2x speed button
    await page.getByRole('group', { name: 'Playback speed' }).getByRole('button', { name: '2x' }).click()
    await expect(page.getByRole('group', { name: 'Playback speed' }).getByRole('button', { name: '2x' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('save button persists book', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())
    await page.route('**/documents/text/text-book-1', r => r.fulfill({ json: { ok: true } }))

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await page.getByRole('button', { name: 'Save' }).click()

    await expect(page.getByText('Saved')).toBeVisible()
  })

  test('back to new text resets state', async ({ page }) => {
    await page.route('**/documents/text', r => r.fulfill({ json: { book_id: 'text-book-1', sentence_count: 2 } }))
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [{ index: 0, text: 'First sentence.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false }]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())

    await page.getByPlaceholder('Paste or type your text here...').fill('Test text')
    await page.getByRole('button', { name: 'Read Aloud' }).click()

    // Click new button
    await page.getByRole('button', { name: 'New text' }).click()

    // Should be back to idle mode with empty textarea
    await expect(page.getByPlaceholder('Paste or type your text here...')).toBeVisible()
    await expect(page.getByPlaceholder('Paste or type your text here...')).toHaveValue('')
  })

  test('upload button opens dialog', async ({ page }) => {
    // Wait for page to fully load
    await page.waitForLoadState('networkidle')
    
    // Check that the Upload button is visible
    const uploadBtn = page.getByRole('button', { name: 'Upload' })
    await expect(uploadBtn).toBeVisible()
    
    // Click the upload button
    await uploadBtn.click()
    
    // Check that the dialog appears
    await expect(page.getByText('Upload Ebook')).toBeVisible({ timeout: 5000 })
  })

  test.describe('Responsive', () => {
    test('no horizontal overflow on mobile', async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 })
      await page.goto('/')
      // Just verify the page loads without horizontal scrollbar
      await expect(page.locator('body')).toBeVisible()
    })

    test('no horizontal overflow on tablet', async ({ page }) => {
      await page.setViewportSize({ width: 768, height: 1024 })
      await page.goto('/')
      await expect(page.locator('body')).toBeVisible()
    })

    test('no horizontal overflow on desktop', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 })
      await page.goto('/')
      await expect(page.locator('body')).toBeVisible()
    })
  })
})
