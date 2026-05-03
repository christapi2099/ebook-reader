import { test, expect } from '@playwright/test'

const MOCK_BOOKS = [
  { id: 'book-1', title: 'Test Book One', author: 'Author A', file_type: 'PDF', page_count: 10 },
  { id: 'book-2', title: 'Test Book Two', author: 'Author B', file_type: 'EPUB', page_count: 25 },
  { id: 'book-3', title: 'Test Book Three', author: null, file_type: 'PDF', page_count: 5 },
]

test.describe('Library Page', () => {
  test('renders loading spinner on mount', async ({ page }) => {
    await page.route('**/library', async route => {
      await new Promise(r => setTimeout(r, 5000))
    })
    await page.goto('/')
    await expect(page.getByText('Loading library…')).toBeVisible()
  })

  test('shows empty state when library is empty', async ({ page }) => {
    await page.route('**/library', r => r.fulfill({ json: [] }))
    await page.goto('/')
    await expect(page.getByText('No books yet')).toBeVisible()
    await expect(page.getByText('Upload a PDF or EPUB to get started')).toBeVisible()
  })

  test('shows error and Retry button on API failure', async ({ page }) => {
    await page.route('**/library', r => r.fulfill({ status: 500, json: {} }))
    await page.goto('/')
    await expect(page.getByText('Could not connect to the backend')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  })

  test('retry button re-fetches library', async ({ page }) => {
    let callCount = 0
    await page.route('**/library', r => {
      callCount++
      if (callCount === 1) return r.fulfill({ status: 500, json: {} })
      return r.fulfill({ json: MOCK_BOOKS })
    })
    await page.goto('/')
    await expect(page.getByText('Could not connect to the backend')).toBeVisible()
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Test Book One')).toBeVisible()
    await expect(page.getByText('Test Book Two')).toBeVisible()
    await expect(page.getByText('Test Book Three')).toBeVisible()
  })

  test('renders book grid', async ({ page }) => {
    await page.route('**/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.goto('/')
    await expect(page.getByText('Test Book One')).toBeVisible()
    await expect(page.getByText('Test Book Two')).toBeVisible()
    await expect(page.getByText('Test Book Three')).toBeVisible()
    await expect(page.getByText('PDF').first()).toBeVisible()
    await expect(page.getByText('EPUB')).toBeVisible()
  })

  test('clicking book navigates to reader', async ({ page }) => {
    await page.route('**/library', r => r.fulfill({ json: MOCK_BOOKS }))
    await page.goto('/')
    await page.getByText('Test Book One').click()
    await expect(page).toHaveURL(/\/reader\/book-1$/)
  })
})
