import { test, expect } from '@playwright/test'

const MOCK_BOOKS = [
  { id: 'book-1', title: 'Test Book One', author: 'Author A', file_type: 'PDF', page_count: 10 },
  { id: 'book-2', title: 'Test Book Two', author: 'Author B', file_type: 'EPUB', page_count: 25 },
  { id: 'book-3', title: 'Test Book Three', author: null, file_type: 'PDF', page_count: 5 },
]

const MOCK_USER_SETTINGS = {
  last_book_id: 'book-1',
  last_sentence_index: 10,
}

test.describe('Library Page', () => {
  test('renders loading spinner on mount', async ({ page }) => {
    await page.route('http://localhost:8000/library', async route => {
      await new Promise(r => setTimeout(r, 2000))
    })
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: MOCK_USER_SETTINGS }))
    await page.goto('/library')
    await expect(page.getByText('Loading library…')).toBeVisible()
  })

  test('shows empty state when library is empty', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ json: [] }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    await expect(page.getByText('No books yet')).toBeVisible({ timeout: 10000 })
  })

  test('shows error and Retry button on API failure', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ status: 500, json: {} }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    await expect(page.getByText('Could not connect to the backend')).toBeVisible({ timeout: 10000 })
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  })

  test('retry button re-fetches library', async ({ page }) => {
    let callCount = 0
    await page.route('http://localhost:8000/library', r => {
      callCount++
      if (callCount === 1) return r.fulfill({ status: 500, json: {} })
      return r.fulfill({ json: MOCK_BOOKS })
    })
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    await expect(page.getByText('Could not connect to the backend')).toBeVisible({ timeout: 10000 })
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Test Book One')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('Test Book Two')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('Test Book Three')).toBeVisible({ timeout: 10000 })
  })

  test('renders book grid', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    await expect(page.getByText('Test Book One')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('Test Book Two')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('Test Book Three')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('PDF').first()).toBeVisible()
    await expect(page.getByText('EPUB')).toBeVisible()
  })

  test('clicking book navigates to reader', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    
    // Wait for the library to load and books to be visible
    await expect(page.getByText('Test Book One')).toBeVisible({ timeout: 10000 })
    
    // Click the book title to navigate to reader
    await page.getByText('Test Book One').first().click()
    
    // Wait for navigation to the reader page
    await page.waitForURL(/\/reader\/book-1/, { timeout: 10000 })
  })

  test('last read card shown when book exists', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: MOCK_USER_SETTINGS }))
    await page.goto('/library')
    
    // Wait for the LastRead card to load (it appears first in the DOM)
    // The LastRead component shows the book title in an h2 element
    await expect(page.locator('h2:has-text("Test Book One")')).toBeVisible({ timeout: 10000 })
    
    // Check for the resume reading card
    await expect(page.getByText('Resume Reading')).toBeVisible({ timeout: 10000 })
  })

  test('no last read card when no book exists', async ({ page }) => {
    await page.route('http://localhost:8000/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.route('http://localhost:8000/user/settings', r => r.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }))
    await page.goto('/library')
    await expect(page.getByText('Resume Reading')).not.toBeVisible({ timeout: 10000 })
  })
})
