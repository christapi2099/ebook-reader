import { test, expect } from '@playwright/test'
import { MOCK_SENTENCES } from './fixtures/mock-data'

test.describe('Error and Edge States', () => {
  test('PDF load failure shows error message', async ({ page }) => {
    await page.route('**/documents/*/sentences', r => r.fulfill({ json: MOCK_SENTENCES }))
    await page.route('**/library/*/progress', r => r.fulfill({ json: { sentence_index: 0 } }))
    await page.route('**/uploads/**', r => r.fulfill({ status: 500, body: 'Internal Server Error' }))
    await page.goto('/reader/book-1')
    await expect(page.locator('.text-red-500')).toBeVisible()
  })
})
