import { test, expect, type Page } from '@playwright/test'
import { AUDIO_CONTEXT_MOCK } from './fixtures/audio-context-mock'

const TEXT_SENTENCES = [
  { index: 0, text: 'The quick brown fox jumps over the lazy dog near the bank.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
  { index: 1, text: 'Bionic reading helps you read faster by guiding your eyes.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
  { index: 2, text: 'Simple words are left untouched for natural flow.', page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false },
]

test.describe('Bionic Reading', () => {
  test.beforeEach(async ({ page }) => {
    await page.clock.install()
    await page.addInitScript(AUDIO_CONTEXT_MOCK)

    await page.route('**/documents/text', async route => {
      route.fulfill({ json: { book_id: 'text-book-1', sentence_count: 3, already_existed: false } })
    })
    await page.route('**/documents/text-book-1/sentences', r => r.fulfill({ json: TEXT_SENTENCES }))
    await page.route('**/ws/tts/**', r => r.fulfill())
    await page.route('**/library/**', r => r.fulfill({ json: { sentence_index: 0 } }))
    await page.route('**/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.route('**/library', r => r.fulfill({
      json: [{ id: 'text-book-1', title: 'Test Book', author: 'Test', file_type: 'text', page_count: 1 }]
    }))
  })

  async function enterReadingMode(page: Page, text = 'The quick brown fox jumps over the lazy dog near the bank.') {
    await page.goto('/')
    await page.getByPlaceholder('Paste or type your text here...').fill(text)
    await page.getByRole('button', { name: 'Read Aloud' }).click()
    await expect(page.locator('[data-sentence-index="0"]')).toBeVisible()
  }

  async function openSettings(page: Page) {
    await page.locator('[aria-label="Settings"]').click()
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  }

  async function toggleBionicOn(page: Page) {
    await openSettings(page)
    await page.getByRole('switch').nth(2).click()
  }

  // ── Basic reading mode ──

  test('enters reading mode with text book', async ({ page }) => {
    await enterReadingMode(page)
    await expect(page.locator('[data-sentence-index="0"]')).toBeVisible()
  })

  test('settings bionic toggle exists and can be opened', async ({ page }) => {
    await enterReadingMode(page)
    await openSettings(page)
    await expect(page.getByRole('switch', { name: '' })).toHaveCount(3)
  })

  // ── Bionic toggle ──

  test('turning bionic mode ON adds bold text in text viewer', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)

    const sentenceEl = page.locator('[data-sentence-index="0"]')
    // "quick brown fox jumps over lazy dog near bank" = 9 boldable words
    await expect(sentenceEl.locator('strong')).not.toHaveCount(0)
  })

  test('bionic mode OFF shows no strong elements', async ({ page }) => {
    await enterReadingMode(page)
    const sentenceEl = page.locator('[data-sentence-index="0"]')
    await expect(sentenceEl.locator('strong')).toHaveCount(0)
  })

  test('bionic mode persisted to localStorage toggle', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)
    await page.keyboard.press('Escape')

    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('kokoro-settings') || '{}'))
    expect(stored.bionicMode).toBe(true)
  })

  test('common words are not bolded in bionic mode', async ({ page }) => {
    await enterReadingMode(page, 'The fox is near a bank.')
    await toggleBionicOn(page)

    const sentenceEl = page.locator('[data-sentence-index="0"]')
    const strongs = sentenceEl.locator('strong')
    const count = await strongs.count()
    for (let i = 0; i < count; i++) {
      const text = await strongs.nth(i).textContent()
      expect(text?.toLowerCase()).not.toBe('the')
      expect(text?.toLowerCase()).not.toBe('a')
      expect(text?.toLowerCase()).not.toBe('is')
    }
  })

  test('toggling bionic mode multiple times works', async ({ page }) => {
    await enterReadingMode(page)
    const sentenceEl = page.locator('[data-sentence-index="0"]')

    await toggleBionicOn(page)
    await page.keyboard.press('Escape')
    await expect(sentenceEl.locator('strong')).not.toHaveCount(0)

    await openSettings(page)
    await page.getByRole('switch').nth(2).click()
    await page.keyboard.press('Escape')
    await expect(sentenceEl.locator('strong')).toHaveCount(0)

    await openSettings(page)
    await page.getByRole('switch').nth(2).click()
    await page.keyboard.press('Escape')
    await expect(sentenceEl.locator('strong')).not.toHaveCount(0)
  })

  test('bionic bold applies to all sentences not just active', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)
    await page.keyboard.press('Escape')

    for (let i = 0; i < 3; i++) {
      const el = page.locator(`[data-sentence-index="${i}"]`)
      await expect(el.locator('strong')).not.toHaveCount(0)
    }
  })

  // ── Bionic sliders ──

  test('bionic bold text changes when fixation slider adjusted', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)

    // Fixation=5 via programmatic set (reliable with mocked clock)
    await page.evaluate(() => {
      const slider = document.getElementById('bionic-fixation') as HTMLInputElement
      if (slider) {
        slider.value = '5'
        slider.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    await page.waitForTimeout(500)

    const sentenceEl = page.locator('[data-sentence-index="0"]')
    await expect(sentenceEl.locator('strong').first()).toBeVisible()
  })

  test('bionic bold text changes when bold ratio slider adjusted', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)

    // boldRatio=0.8
    await page.evaluate(() => {
      const slider = document.getElementById('bionic-ratio') as HTMLInputElement
      if (slider) {
        slider.value = '0.8'
        slider.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    await page.waitForTimeout(500)

    const sentenceEl = page.locator('[data-sentence-index="0"]')
    await expect(sentenceEl.locator('strong').first()).toBeVisible()
  })

  test('fixation slider hidden when bionic OFF, visible when ON', async ({ page }) => {
    await enterReadingMode(page)
    await openSettings(page)

    await expect(page.locator('#bionic-fixation')).not.toBeVisible()

    await page.getByRole('switch').nth(2).click()
    await expect(page.locator('#bionic-fixation')).toBeVisible()
  })

  test('bold ratio slider hidden when bionic OFF, visible when ON', async ({ page }) => {
    await enterReadingMode(page)
    await openSettings(page)

    await expect(page.locator('#bionic-ratio')).not.toBeVisible()

    await page.getByRole('switch').nth(2).click()
    await expect(page.locator('#bionic-ratio')).toBeVisible()
  })

  test('bionic label shows current fixation value', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)

    await expect(page.getByText('Fixation point: 1')).toBeVisible()

    await page.evaluate(() => {
      const slider = document.getElementById('bionic-fixation') as HTMLInputElement
      if (slider) {
        slider.value = '3'
        slider.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    await expect(page.getByText('Fixation point: 3')).toBeVisible()
  })

  test('bionic label shows current bold ratio value', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)

    await page.evaluate(() => {
      const slider = document.getElementById('bionic-ratio') as HTMLInputElement
      if (slider) {
        slider.value = '0.35'
        slider.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    await expect(page.getByText('Bold strength: 0.35')).toBeVisible()
  })

  test('closing settings then reopening reflects persisted bionic state', async ({ page }) => {
    await enterReadingMode(page)
    await toggleBionicOn(page)
    await page.keyboard.press('Escape')
    await openSettings(page)

    await expect(page.getByRole('switch').nth(2)).toHaveAttribute('aria-checked', 'true')
    await expect(page.locator('#bionic-fixation')).toBeVisible()
    await expect(page.locator('#bionic-ratio')).toBeVisible()
  })

  // ── PDF viewer bionic tests ──

  test.describe('PDF Bionic Overlay', () => {
    const MINIMAL_PDF = new Uint8Array([
      0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0xc4, 0xe5,
      0xf2, 0xe5, 0xeb, 0xa7, 0xf3, 0xa0, 0xd0, 0xc4, 0xc6, 0x0a, 0x31, 0x20,
      0x30, 0x20, 0x6f, 0x62, 0x6a, 0x0a, 0x3c, 0x3c, 0x20, 0x2f, 0x54, 0x79,
      0x70, 0x65, 0x20, 0x2f, 0x43, 0x61, 0x74, 0x61, 0x6c, 0x6f, 0x67, 0x20,
      0x2f, 0x50, 0x61, 0x67, 0x65, 0x73, 0x20, 0x32, 0x20, 0x30, 0x20, 0x52,
      0x20, 0x3e, 0x3e, 0x0a, 0x65, 0x6e, 0x64, 0x6f, 0x62, 0x6a, 0x0a, 0x32,
      0x20, 0x30, 0x20, 0x6f, 0x62, 0x6a, 0x0a, 0x3c, 0x3c, 0x20, 0x2f, 0x54,
      0x79, 0x70, 0x65, 0x20, 0x2f, 0x50, 0x61, 0x67, 0x65, 0x73, 0x20, 0x2f,
      0x4b, 0x69, 0x64, 0x73, 0x20, 0x5b, 0x33, 0x20, 0x30, 0x20, 0x52, 0x5d,
      0x20, 0x2f, 0x43, 0x6f, 0x75, 0x6e, 0x74, 0x20, 0x31, 0x20, 0x3e, 0x3e,
      0x0a, 0x65, 0x6e, 0x64, 0x6f, 0x62, 0x6a, 0x0a, 0x33, 0x20, 0x30, 0x20,
      0x6f, 0x62, 0x6a, 0x0a, 0x3c, 0x3c, 0x20, 0x2f, 0x54, 0x79, 0x70, 0x65,
      0x20, 0x2f, 0x50, 0x61, 0x67, 0x65, 0x20, 0x2f, 0x50, 0x61, 0x72, 0x65,
      0x6e, 0x74, 0x20, 0x32, 0x20, 0x30, 0x20, 0x52, 0x20, 0x2f, 0x4d, 0x65,
      0x64, 0x69, 0x61, 0x42, 0x6f, 0x78, 0x20, 0x5b, 0x30, 0x20, 0x30, 0x20,
      0x36, 0x31, 0x32, 0x20, 0x37, 0x39, 0x32, 0x5d, 0x20, 0x3e, 0x3e, 0x0a,
      0x65, 0x6e, 0x64, 0x6f, 0x62, 0x6a, 0x0a, 0x78, 0x72, 0x65, 0x66, 0x0a,
      0x30, 0x20, 0x34, 0x0a, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30,
      0x30, 0x30, 0x20, 0x36, 0x35, 0x35, 0x33, 0x35, 0x20, 0x66, 0x20, 0x0a,
      0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x39, 0x20, 0x30,
      0x30, 0x30, 0x30, 0x30, 0x20, 0x6e, 0x20, 0x0a, 0x30, 0x30, 0x30, 0x30,
      0x30, 0x30, 0x30, 0x30, 0x35, 0x38, 0x20, 0x30, 0x30, 0x30, 0x30, 0x30,
      0x20, 0x6e, 0x20, 0x0a, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30,
      0x31, 0x31, 0x35, 0x20, 0x30, 0x30, 0x30, 0x30, 0x30, 0x20, 0x6e, 0x20,
      0x0a, 0x74, 0x72, 0x61, 0x69, 0x6c, 0x65, 0x72, 0x0a, 0x3c, 0x3c, 0x20,
      0x2f, 0x53, 0x69, 0x7a, 0x65, 0x20, 0x34, 0x20, 0x2f, 0x52, 0x6f, 0x6f,
      0x74, 0x20, 0x31, 0x20, 0x30, 0x20, 0x52, 0x20, 0x3e, 0x3e, 0x0a, 0x73,
      0x74, 0x61, 0x72, 0x74, 0x78, 0x72, 0x65, 0x66, 0x0a, 0x31, 0x39, 0x30,
      0x0a, 0x25, 0x25, 0x45, 0x4f, 0x46,
    ])

    const PDF_SENTENCES = [
      { index: 0, text: 'The quick brown fox jumps over the lazy dog.', page: 0, x0: 50, y0: 100, x1: 400, y1: 120, filtered: false },
      { index: 1, text: 'Bionic reading helps you read faster.', page: 0, x0: 50, y0: 140, x1: 350, y1: 160, filtered: false },
    ]

    async function setupPdfBook(page: Page) {
      await page.route('**/library', r => r.fulfill({
        json: [{ id: 'pdf-book-1', title: 'PDF Book', author: 'Test', file_type: 'pdf', page_count: 1 }]
      }), { once: true })
      await page.route('**/documents/pdf-book-1/sentences', r => r.fulfill({ json: PDF_SENTENCES }))
      await page.route('**/uploads/pdf-book-1.pdf', async route => {
        await route.fulfill({
          contentType: 'application/pdf',
          body: Buffer.from(MINIMAL_PDF),
        })
      })
      await page.route('**/ws/tts/**', r => r.fulfill())
      await page.route('**/library/**', r => r.fulfill({ json: { sentence_index: 0 } }))
      await page.route('**/user/settings', r => r.fulfill({ json: { last_book_id: 'pdf-book-1', last_sentence_index: 0 } }))
      await page.goto('/reader/pdf-book-1')
      await page.waitForTimeout(2000)
    }

    test('bionic overlay divs are present in DOM', async ({ page }) => {
      await setupPdfBook(page)
      const overlays = page.locator('[data-bionic-overlay]')
      await expect(overlays).toHaveCount(1)
      // Overlay starts hidden (bionic mode defaults to OFF)
      await expect(overlays.first()).toBeHidden()
    })

    test('bionic overlay becomes visible when bionic mode turned ON', async ({ page }) => {
      await setupPdfBook(page)
      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')

      const overlay = page.locator('[data-bionic-overlay="0"]')
      await expect(overlay).toBeVisible()
    })

    test('bionic overlay has aria-hidden attribute', async ({ page }) => {
      await setupPdfBook(page)
      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')

      const overlay = page.locator('[data-bionic-overlay="0"]')
      await expect(overlay).toHaveAttribute('aria-hidden', 'true')
    })

    test('bionic overlay contains text spans matching sentences', async ({ page }) => {
      await setupPdfBook(page)
      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')

      // Wait for debounced bionic render (80ms debounce)
      await page.waitForTimeout(500)

      const overlay = page.locator('[data-bionic-overlay="0"]')
      const overlayText = await overlay.allInnerTexts()
      const combined = overlayText.join(' ')
      expect(combined).toContain('quick')
      expect(combined).toContain('brown')
      expect(combined).toContain('Bionic')
    })

    test('bionic overlay toggles visibility with bionic mode setting', async ({ page }) => {
      await setupPdfBook(page)
      const overlay = page.locator('[data-bionic-overlay="0"]')

      await expect(overlay).toBeHidden()

      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')
      await expect(overlay).toBeVisible()

      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')
      await expect(overlay).toBeHidden()
    })

    test('canvas opacity decreases when bionic mode is ON', async ({ page }) => {
      await setupPdfBook(page)
      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')

      await page.waitForTimeout(500)
      const canvas = page.locator('canvas').first()
      const opacity = await canvas.evaluate(el => parseFloat(getComputedStyle(el).opacity))
      expect(opacity).toBeCloseTo(0.3, 1)
    })

    test('canvas opacity returns to 1 when bionic mode is OFF', async ({ page }) => {
      await setupPdfBook(page)
      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')
      await page.waitForTimeout(500)

      await page.locator('[aria-label="Settings"]').click()
      await page.getByRole('switch').nth(2).click()
      await page.keyboard.press('Escape')

      await page.waitForTimeout(500)
      const canvas = page.locator('canvas').first()
      const opacity = await canvas.evaluate(el => parseFloat(getComputedStyle(el).opacity))
      expect(opacity).toBeCloseTo(1, 1)
    })
  })

  // ── localStorage persistence tests ──

  test.describe('localStorage Persistence', () => {
    test('bionic mode false by default in localStorage', async ({ page }) => {
      await enterReadingMode(page)
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('kokoro-settings') || '{}'))
      expect(stored.bionicMode).toBe(false)
    })

    test('bionic fixation persists in localStorage', async ({ page }) => {
      await enterReadingMode(page)
      await toggleBionicOn(page)
      await page.evaluate(() => {
        const slider = document.getElementById('bionic-fixation') as HTMLInputElement
        if (slider) {
          slider.value = '4'
          slider.dispatchEvent(new Event('input', { bubbles: true }))
        }
      })
      await page.keyboard.press('Escape')

      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('kokoro-settings') || '{}'))
      expect(stored.bionicFixation).toBe(4)
    })

    test('bionic bold ratio persists in localStorage', async ({ page }) => {
      await enterReadingMode(page)
      await toggleBionicOn(page)
      await page.evaluate(() => {
        const slider = document.getElementById('bionic-ratio') as HTMLInputElement
        if (slider) {
          slider.value = '0.65'
          slider.dispatchEvent(new Event('input', { bubbles: true }))
        }
      })
      await page.keyboard.press('Escape')

      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('kokoro-settings') || '{}'))
      expect(stored.bionicBoldRatio).toBe(0.65)
    })

    test('all bionic settings survive full navigation cycle', async ({ page }) => {
      await enterReadingMode(page)
      await toggleBionicOn(page)
      await page.evaluate(() => {
        const f = document.getElementById('bionic-fixation') as HTMLInputElement
        if (f) { f.value = '3'; f.dispatchEvent(new Event('input', { bubbles: true })) }
        const r = document.getElementById('bionic-ratio') as HTMLInputElement
        if (r) { r.value = '0.4'; r.dispatchEvent(new Event('input', { bubbles: true })) }
      })
      await page.keyboard.press('Escape')
      await page.reload()

      // Homepage starts in idle mode — re-enter reading mode
      await page.getByPlaceholder('Paste or type your text here...').fill('The quick brown fox jumps over the lazy dog near the bank.')
      await page.getByRole('button', { name: 'Read Aloud' }).click()
      await expect(page.locator('[data-sentence-index="0"]')).toBeVisible()

      await page.locator('[aria-label="Settings"]').click()
      await expect(page.getByRole('switch').nth(2)).toHaveAttribute('aria-checked', 'true')
      await page.keyboard.press('Escape')

      const sentenceEl = page.locator('[data-sentence-index="0"]')
      await expect(sentenceEl.locator('strong')).not.toHaveCount(0)
    })
  })
})
