import { test, expect, type Page } from '@playwright/test'

/**
 * Library folders UI (handoff §5 task 2).
 *
 * These tests stand up a fake folders backend with `page.route` that follows the
 * real contract — including case-insensitive duplicate names, the 60-character
 * cap and the `unfiled_books` count on delete — so every number the page shows
 * still comes from a server response, never from the client.
 */

const NAME_REQUIRED = 'Folder name is required'
const NAME_TOO_LONG = 'Folder name must be at most 60 characters'
const NAME_TAKEN = 'A folder with that name already exists'
const MAX_NAME_LENGTH = 60

/** The page is served from /library, so route patterns must name the backend host. */
const API = 'http://localhost:8000'

interface MockBook {
  id: string
  title: string
  author: string | null
  file_type: string
  page_count: number
  folder_id: number | null
}

interface MockFolder {
  id: number
  name: string
  created_at: string
}

interface CapturedRequest {
  method: string
  path: string
  body: Record<string, unknown> | null
}

class FakeFoldersBackend {
  books: MockBook[]
  folders: MockFolder[]
  requests: CapturedRequest[] = []
  private nextId: number

  constructor(books: MockBook[], folders: MockFolder[]) {
    this.books = books.map(book => ({ ...book }))
    this.folders = folders.map(folder => ({ ...folder }))
    this.nextId = Math.max(0, ...this.folders.map(folder => folder.id)) + 1
  }

  /** The backend counts books per folder and sorts folders by name. */
  private folderPayload() {
    return [...this.folders]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(folder => ({
        ...folder,
        book_count: this.books.filter(book => book.folder_id === folder.id).length,
      }))
  }

  private nameError(name: unknown, excludeId?: number): string | null {
    const trimmed = String(name ?? '').trim()
    if (!trimmed) return NAME_REQUIRED
    if (trimmed.length > MAX_NAME_LENGTH) return NAME_TOO_LONG
    const clash = this.folders.some(
      folder =>
        folder.id !== excludeId && folder.name.toLowerCase() === trimmed.toLowerCase(),
    )
    return clash ? NAME_TAKEN : null
  }

  private note(page: Page, method: string, path: string, body: Record<string, unknown> | null) {
    this.requests.push({ method, path, body })
    void page
  }

  async install(page: Page) {
    await page.route(`${API}/user/settings`, route =>
      route.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }),
    )

    await page.route(`${API}/library`, route => route.fulfill({ json: this.books }))

    await page.route(`${API}/library/*/folder`, async route => {
      const request = route.request()
      const body = request.postDataJSON() as { folder_id: number | null }
      const bookId = new URL(request.url()).pathname.split('/')[2]
      this.note(page, request.method(), `/library/${bookId}/folder`, body)
      const book = this.books.find(candidate => candidate.id === bookId)
      if (!book) return route.fulfill({ status: 404, json: { detail: 'Book not found' } })
      if (body.folder_id !== null && !this.folders.some(f => f.id === body.folder_id)) {
        return route.fulfill({ status: 404, json: { detail: 'Folder not found' } })
      }
      book.folder_id = body.folder_id
      return route.fulfill({ json: { ok: true, folder_id: book.folder_id } })
    })

    await page.route(`${API}/folders`, async route => {
      const request = route.request()
      if (request.method() === 'GET') {
        this.note(page, 'GET', '/folders', null)
        return route.fulfill({ json: this.folderPayload() })
      }

      const body = request.postDataJSON() as { name: string }
      this.note(page, 'POST', '/folders', body)
      const problem = this.nameError(body.name)
      if (problem) return route.fulfill({ status: problem === NAME_TAKEN ? 409 : 400, json: { detail: problem } })
      const folder: MockFolder = {
        id: this.nextId++,
        name: body.name.trim(),
        created_at: new Date().toISOString(),
      }
      this.folders.push(folder)
      return route.fulfill({ status: 201, json: { ...folder, book_count: 0 } })
    })

    await page.route(`${API}/folders/*`, async route => {
      const request = route.request()
      const folderId = Number(new URL(request.url()).pathname.split('/').pop())
      const folder = this.folders.find(candidate => candidate.id === folderId)
      if (!folder) return route.fulfill({ status: 404, json: { detail: 'Folder not found' } })

      if (request.method() === 'PATCH') {
        const body = request.postDataJSON() as { name: string }
        this.note(page, 'PATCH', `/folders/${folderId}`, body)
        const problem = this.nameError(body.name, folderId)
        if (problem) {
          return route.fulfill({ status: problem === NAME_TAKEN ? 409 : 400, json: { detail: problem } })
        }
        folder.name = body.name.trim()
        const count = this.books.filter(book => book.folder_id === folderId).length
        return route.fulfill({ json: { ...folder, book_count: count } })
      }

      if (request.method() === 'DELETE') {
        this.note(page, 'DELETE', `/folders/${folderId}`, null)
        const unfiled = this.books.filter(book => book.folder_id === folderId)
        unfiled.forEach(book => (book.folder_id = null))
        this.folders = this.folders.filter(candidate => candidate.id !== folderId)
        return route.fulfill({ json: { ok: true, unfiled_books: unfiled.length } })
      }

      return route.fulfill({ status: 405, json: { detail: 'Method not allowed' } })
    })
  }
}

const BOOKS: MockBook[] = [
  {
    id: 'book-1',
    title: 'Deep Work',
    author: 'Cal Newport',
    file_type: 'PDF',
    page_count: 10,
    folder_id: null,
  },
  {
    id: 'book-2',
    title: 'The Shallows',
    author: null,
    file_type: 'EPUB',
    page_count: 20,
    folder_id: 2,
  },
]

const FOLDERS: MockFolder[] = [
  { id: 2, name: 'Reading list', created_at: '2026-01-01T00:00:00Z' },
  { id: 3, name: 'Someday', created_at: '2026-01-02T00:00:00Z' },
]

/**
 * Navigate and wait for the client-rendered content.
 *
 * The route is server-rendered, so the "New folder" button and the heading
 * exist in the initial HTML before hydration. Waiting on those would let a
 * click land on a button with no listeners yet; the folder tiles only appear
 * once the client fetch has resolved, which means hydration is done.
 */
async function openLibrary(page: Page, backend = new FakeFoldersBackend(BOOKS, FOLDERS)) {
  await backend.install(page)
  await page.goto('/library')
  await expect(page.getByLabel('Open folder Reading list, 1 book')).toBeVisible()
  return backend
}

function openMoveDialog(page: Page, bookTitle: string) {
  return page.getByLabel(`Book options for ${bookTitle}`).click().then(() =>
    page.getByRole('button', { name: 'Move to folder', exact: true }).click(),
  )
}

test.describe('Library folders', () => {
  test('@critical shows a tile per folder with the count the server reported', async ({ page }) => {
    await openLibrary(page)

    await expect(page.getByLabel('Open folder Reading list, 1 book')).toBeVisible()
    await expect(page.getByLabel('Open folder Someday, 0 books')).toBeVisible()
  })

  test('@critical opening a folder filters the grid and shows a breadcrumb', async ({ page }) => {
    await openLibrary(page)

    await page.getByLabel('Open folder Reading list, 1 book').click()

    const breadcrumb = page.getByRole('navigation', { name: 'Breadcrumb' })
    await expect(breadcrumb.getByRole('button', { name: 'All books' })).toBeVisible()
    await expect(breadcrumb.getByText('Reading list')).toBeVisible()
    await expect(page.getByText('The Shallows')).toBeVisible()
    await expect(page.getByText('Deep Work')).toBeHidden()
  })

  test('breadcrumb goes back to all books', async ({ page }) => {
    await openLibrary(page)

    await page.getByLabel('Open folder Reading list, 1 book').click()
    await page.getByRole('navigation', { name: 'Breadcrumb' })
      .getByRole('button', { name: 'All books' })
      .click()

    await expect(page.getByText('Deep Work')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toBeHidden()
    await expect(page.getByLabel('Open folder Reading list, 1 book')).toBeVisible()
  })

  test('an empty folder shows a real empty state, not a blank area', async ({ page }) => {
    await openLibrary(page)

    await page.getByLabel('Open folder Someday, 0 books').click()

    await expect(page.getByText('This folder is empty')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Back to all books' })).toBeVisible()
    await expect(page.getByText('No books yet')).toBeHidden()
  })

  test('@critical creates a folder and renders the count the refreshed list reports', async ({ page }) => {
    const backend = await openLibrary(page)

    await page.getByRole('button', { name: 'New folder' }).click()
    await page.getByLabel('Folder name').fill('Later')
    await page.getByRole('button', { name: 'Create' }).click()

    await expect(page.getByLabel('Open folder Later, 0 books')).toBeVisible()
    expect(backend.requests).toContainEqual({ method: 'POST', path: '/folders', body: { name: 'Later' } })
  })

  test('a blank name shows the backend message, not a status code', async ({ page }) => {
    await openLibrary(page)

    await page.getByRole('button', { name: 'New folder' }).click()
    await page.getByRole('button', { name: 'Create' }).click()

    await expect(page.getByRole('alert')).toHaveText(NAME_REQUIRED)
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByText(/HTTP \d\d\d/)).toBeHidden()
  })

  test('@critical a duplicate name is rejected case-insensitively with the backend message', async ({ page }) => {
    await openLibrary(page)

    await page.getByRole('button', { name: 'New folder' }).click()
    await page.getByLabel('Folder name').fill('reading LIST')
    await page.getByRole('button', { name: 'Create' }).click()

    await expect(page.getByRole('alert')).toHaveText(NAME_TAKEN)
  })

  test('a too-long name shows the backend message', async ({ page }) => {
    await openLibrary(page)

    await page.getByRole('button', { name: 'New folder' }).click()
    await page.getByLabel('Folder name').fill('x'.repeat(MAX_NAME_LENGTH + 1))
    await page.getByRole('button', { name: 'Create' }).click()

    await expect(page.getByRole('alert')).toHaveText(NAME_TOO_LONG)
  })

  test('renaming onto an existing name shows the backend message', async ({ page }) => {
    const backend = await openLibrary(page)

    await page.getByLabel('Folder options for Someday').click()
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    await page.getByLabel('Folder name').fill('Reading list')
    await page.getByRole('button', { name: 'Rename' }).click()

    await expect(page.getByRole('alert')).toHaveText(NAME_TAKEN)
    expect(backend.requests.some(request => request.method === 'PATCH')).toBe(true)
  })

  test('renaming a folder updates its tile', async ({ page }) => {
    await openLibrary(page)

    await page.getByLabel('Folder options for Someday').click()
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    await page.getByLabel('Folder name').fill('Later')
    await page.getByRole('button', { name: 'Rename' }).click()

    await expect(page.getByLabel('Open folder Later, 0 books')).toBeVisible()
    await expect(page.getByLabel('Folder options for Someday')).toBeHidden()
  })

  // The dialog is the accessible path; drag-and-drop below is only an extra.
  test('@critical the move dialog files a book with the keyboard', async ({ page }) => {
    const backend = await openLibrary(page)

    const options = page.getByLabel('Book options for Deep Work')
    await options.focus()
    await page.keyboard.press('Enter')
    await page.getByRole('button', { name: 'Move to folder', exact: true }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('radio', { name: /Reading list/ })).toBeVisible()

    await dialog.getByRole('radio', { name: /Reading list/ }).check()
    await dialog.getByRole('button', { name: 'Move' }).click()

    await expect(dialog).toBeHidden()
    expect(backend.requests).toContainEqual({
      method: 'POST',
      path: '/library/book-1/folder',
      body: { folder_id: 2 },
    })
    // The count comes back from the server and is re-rendered.
    await expect(page.getByLabel('Open folder Reading list, 2 books')).toBeVisible()
  })

  test('the move dialog can take a book out of every folder', async ({ page }) => {
    const backend = await openLibrary(page)

    await openMoveDialog(page, 'The Shallows')
    await page.getByRole('radio', { name: /No folder/ }).check()
    await page.getByRole('button', { name: 'Move' }).click()

    expect(backend.requests).toContainEqual({
      method: 'POST',
      path: '/library/book-2/folder',
      body: { folder_id: null },
    })
    await expect(page.getByLabel('Open folder Reading list, 0 books')).toBeVisible()
  })

  test('Escape closes only the move dialog and returns focus to the book menu', async ({ page }) => {
    await openLibrary(page)

    const options = page.getByLabel('Book options for Deep Work')
    await openMoveDialog(page, 'Deep Work')
    await expect(page.getByRole('dialog')).toBeVisible()

    await page.keyboard.press('Escape')

    await expect(page.getByRole('dialog')).toBeHidden()
    await expect(options).toBeFocused()
  })

  test('@critical dragging a book onto a folder tile files it', async ({ page }) => {
    const backend = await openLibrary(page)

    await page.getByLabel('Deep Work by Cal Newport').dragTo(
      page.getByLabel('Open folder Reading list, 1 book'),
    )

    await expect
      .poll(() => backend.requests.filter(request => request.path === '/library/book-1/folder').length)
      .toBeGreaterThan(0)
    expect(backend.requests).toContainEqual({
      method: 'POST',
      path: '/library/book-1/folder',
      body: { folder_id: 2 },
    })
    await expect(page.getByLabel('Open folder Reading list, 2 books')).toBeVisible()
  })

  test('dragging a book onto the breadcrumb takes it out of the folder', async ({ page }) => {
    const backend = await openLibrary(page)
    await page.getByLabel('Open folder Reading list, 1 book').click()

    await page.getByLabel('The Shallows by Unknown').dragTo(
      page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('button', { name: 'All books' }),
    )

    await expect
      .poll(() => backend.requests.filter(request => request.path === '/library/book-2/folder').length)
      .toBeGreaterThan(0)
    expect(backend.requests).toContainEqual({
      method: 'POST',
      path: '/library/book-2/folder',
      body: { folder_id: null },
    })
  })

  test('@critical deleting a folder keeps its books and offers an undo', async ({ page }) => {
    const backend = await openLibrary(page)
    page.on('dialog', dialog => dialog.accept())

    await page.getByLabel('Folder options for Reading list').click()
    await page.getByRole('menuitem', { name: 'Delete folder' }).click()

    await expect.poll(() => backend.requests.some(r => r.method === 'DELETE')).toBe(true)
    const toast = page.getByRole('status').filter({ hasText: /Deleted/ })
    await expect(toast).toBeVisible()
    await expect(toast.getByText('1 book moved to All books')).toBeVisible()
    // The book itself is still there.
    await expect(page.getByText('The Shallows')).toBeVisible()

    await page.getByRole('button', { name: 'Undo' }).click()

    await expect(page.getByLabel('Open folder Reading list, 1 book')).toBeVisible()
    expect(backend.requests).toContainEqual({
      method: 'POST',
      path: '/folders',
      body: { name: 'Reading list' },
    })
  })

  test('tiles stay reachable by keyboard and their controls meet the 44px target', async ({ page }) => {
    await openLibrary(page)

    const options = page.getByLabel('Folder options for Reading list')
    await options.focus()
    await page.keyboard.press('Enter')
    const menu = page.getByRole('menu')
    await expect(menu).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(menu).toBeHidden()
    await expect(options).toBeFocused()

    for (const target of [
      page.getByLabel('Open folder Reading list, 1 book'),
      options,
      page.getByRole('button', { name: 'New folder' }),
    ]) {
      const box = await target.boundingBox()
      expect(box, 'control should have a box').not.toBeNull()
      expect(box!.height).toBeGreaterThanOrEqual(44)
    }
  })

  test('@critical backend down: error and Retry, and no folder controls pretending otherwise', async ({ page }) => {
    await page.route(`${API}/user/settings`, route =>
      route.fulfill({ json: { last_book_id: null, last_sentence_index: 0 } }),
    )
    await page.route(`${API}/library`, route => route.fulfill({ status: 500, json: {} }))
    await page.route(`${API}/folders`, route => route.fulfill({ status: 500, json: {} }))

    await page.goto('/library')

    // Client-rendered: reaching this text means hydration and the failed fetch
    // have both happened, so the assertions below are not racing the page.
    await expect(page.getByText('Could not connect to the backend', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
    await expect(page.getByText('Folders')).toBeHidden()
  })
})
