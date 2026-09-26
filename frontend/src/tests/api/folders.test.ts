import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  FOLDER_NAME_MAX_LENGTH,
  createFolder,
  deleteFolder,
  getFolders,
  renameFolder,
  setBookFolder,
  type Folder,
} from '$lib/api'
import { ApiError, toDetailMessage } from '$lib/utils/errors'

const READING_LIST: Folder = {
  id: 7,
  name: 'Reading list',
  created_at: '2026-01-01T00:00:00Z',
  book_count: 3,
}

function mockJson(body: unknown, status = 200, statusText = 'OK') {
  ;(global.fetch as any).mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  })
}

describe('Folders API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = vi.fn()
  })

  it('lists folders from GET /folders', async () => {
    mockJson([READING_LIST])

    await expect(getFolders()).resolves.toEqual([READING_LIST])
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/folders', {
      headers: { 'Content-Type': 'application/json' },
    })
  })

  it('creates a folder with POST /folders', async () => {
    mockJson(READING_LIST, 201, 'Created')

    await expect(createFolder('Reading list')).resolves.toEqual(READING_LIST)
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Reading list' }),
    })
  })

  it('renames a folder with PATCH /folders/{id}', async () => {
    mockJson({ ...READING_LIST, name: 'Later' })

    await expect(renameFolder(7, 'Later')).resolves.toEqual({ ...READING_LIST, name: 'Later' })
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/folders/7', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Later' }),
    })
  })

  it('deletes a folder and reports how many books were unfiled', async () => {
    mockJson({ ok: true, unfiled_books: 2 })

    await expect(deleteFolder(7)).resolves.toEqual({ ok: true, unfiled_books: 2 })
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/folders/7',
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  it('files a book with POST /library/{book_id}/folder', async () => {
    mockJson({ ok: true, folder_id: 7 })

    await expect(setBookFolder('book-1', 7)).resolves.toEqual({ ok: true, folder_id: 7 })
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8000/library/book-1/folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_id: 7 }),
    })
  })

  it('unfiles a book by sending folder_id: null', async () => {
    mockJson({ ok: true, folder_id: null })

    await setBookFolder('book-1', null)

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/library/book-1/folder',
      expect.objectContaining({ body: JSON.stringify({ folder_id: null }) })
    )
  })

  it('mirrors the backend name cap', () => {
    expect(FOLDER_NAME_MAX_LENGTH).toBe(60)
  })

  // The server explains itself in `detail`; the UI must show that text.
  for (const [status, statusText, detail] of [
    [400, 'Bad Request', 'Folder name is required'],
    [400, 'Bad Request', 'Folder name must be at most 60 characters'],
    [409, 'Conflict', 'A folder with that name already exists'],
  ] as const) {
    it(`surfaces the server detail for ${status} "${detail}"`, async () => {
      mockJson({ detail }, status, statusText)

      const error = await createFolder('  ').catch((e: unknown) => e)

      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(status)
      expect((error as Error).message).toBe(detail)
    })
  }

  it('falls back to the status line when the body has no detail', async () => {
    ;(global.fetch as any).mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({}),
    })

    await expect(createFolder('Reading list')).rejects.toThrow('HTTP 500: Internal Server Error')
  })
})

describe('toDetailMessage', () => {
  it('prefers the server detail over the generic 400 copy', () => {
    expect(toDetailMessage(new ApiError(400, 'Bad Request', 'Folder name is required'))).toBe(
      'Folder name is required'
    )
  })

  it('falls back to friendly copy when the server sent no detail', () => {
    expect(toDetailMessage(new ApiError(400, 'Bad Request'))).toBe('The request was invalid')
    expect(toDetailMessage(new TypeError('Failed to fetch'))).toBe('Could not connect to the backend')
  })
})
