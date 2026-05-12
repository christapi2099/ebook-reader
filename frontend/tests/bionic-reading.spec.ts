import { test, expect } from '@playwright/test'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'
import { WsDriver } from './fixtures/ws-driver'

test.describe('Bionic Reading', () => {
  let driver: WsDriver

  test.beforeEach(async ({ page }) => {
    driver = new WsDriver()
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)

    await page.route('**/documents/text', async route => {
      route.fulfill({ json: { book_id: 'text-book-1', sentence_count: 3, already_existed: false } })
    })
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({
      json: [
        { index: 0, text: 'The quick brown fox jumps over the lazy dog near the bank.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 1, text: 'Bionic reading helps you read faster by guiding your eyes.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
        { index: 2, text: 'Simple words are left untouched for natural flow.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
      ]
    }))
    await page.route('**/ws/tts/**', r => r.fulfill())
    await page.route('**/library/**', r => r.fulfill({ json: { sentence_index: 0 } }))
    await page.route('**/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.route('**/library', r => r.fulfill({
      json: [{ id: 'text-book-1', title: 'Test Book', author: 'Test', file_type: 'text', page_count: 1 }]
    }))
  })

  test('enters reading mode with text book', async ({ page }) => {
    await page.goto('/')
    await page.getByPlaceholder('Paste or type your text here...').fill('The quick brown fox.')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.getByText('The quick brown fox.')).toBeVisible()
  })

  test('settings bionic toggle exists and can be opened', async ({ page }) => {
    await page.goto('/')
    await page.getByPlaceholder('Paste or type your text here...').fill('The quick brown fox jumps over the lazy dog near the bank.')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.getByText('The quick brown fox')).toBeVisible()

    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByText('Bionic Reading')).toBeVisible()
    await expect(page.getByRole('switch', { name: '' })).toHaveCount(3)
  })

  test('turning bionic mode ON adds bold text in text viewer', async ({ page }) => {
    await page.goto('/')
    await page.getByPlaceholder('Paste or type your text here...').fill('The quick brown fox jumps over the lazy dog near the bank.')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.getByText('The quick brown fox')).toBeVisible()

    await page.getByRole('button', { name: 'Settings' }).click()

    const bionicSwitch = page.getByRole('switch').nth(2)
    await bionicSwitch.click()

    const sentenceEl = page.locator('[data-sentence-index="0"]')
    await expect(sentenceEl.locator('strong')).toHaveCount(4)
  })

  test('bionic mode persists after page reload via localStorage', async ({ page, context }) => {
    await page.goto('/')
    await page.getByPlaceholder('Paste or type your text here...').fill('The quick brown fox jumps over the lazy dog near the bank.')
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.getByText('The quick brown fox')).toBeVisible()

    await page.getByRole('button', { name: 'Settings' }).click()
    await page.getByRole('switch').nth(2).click()
    await page.keyboard.press('Escape')

    await page.reload()
    await expect(page.getByText('The quick brown fox')).toBeVisible()
    await page.getByRole('button', { name: 'Settings' }).click()
    await expect(page.getByRole('switch').nth(2)).toHaveAttribute('aria-checked', 'true')
  })
})
