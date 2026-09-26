import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within, cleanup } from '@testing-library/svelte'
import LibraryPage from '../../routes/library/+page.svelte'
import type { Book, Sentence } from '$lib/api'
import * as api from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getLibrary: vi.fn(),
    getFolders: vi.fn(),
    getUserSettings: vi.fn(),
    getProgress: vi.fn(),
    getSentences: vi.fn(),
  }
})

import { goto } from '$app/navigation'

const BOOKS: Book[] = [
  { id: 'book-1', title: 'Deep Work', author: null, file_type: 'PDF', page_count: 10, folder_id: null },
  { id: 'book-2', title: 'The Shallows', author: null, file_type: 'text', page_count: 20, folder_id: null },
]

function sentences(count: number): Sentence[] {
  return Array.from({ length: count }, (_, index) => ({
    index,
    text: `Sentence ${index}`,
    page: 0,
    x0: 0,
    y0: 0,
    x1: 0,
    y1: 0,
    filtered: false,
    chapter: 0,
  }))
}

/** Render the library and wait for the cards to appear. */
async function renderLibrary() {
  render(LibraryPage)
  await waitFor(() => expect(screen.getByText('Deep Work')).toBeTruthy())
}

function cardFor(title: string) {
  return screen.getByLabelText(`${title} by Unknown`)
}

describe('Library reading progress', () => {
  beforeEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.mocked(api.getLibrary).mockResolvedValue(BOOKS)
    vi.mocked(api.getFolders).mockResolvedValue([])
    vi.mocked(api.getUserSettings).mockResolvedValue({ last_book_id: null, last_sentence_index: 0 })
    vi.mocked(api.getProgress).mockResolvedValue(0)
    vi.mocked(api.getSentences).mockResolvedValue([])
  })

  it('shows the position the server reported for each book', async () => {
    vi.mocked(api.getProgress).mockImplementation(async (bookId: string) =>
      bookId === 'book-1' ? 30 : 0,
    )
    vi.mocked(api.getSentences).mockResolvedValue(sentences(120))

    await renderLibrary()

    const bar = await within(cardFor('Deep Work')).findByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('25')
    expect(vi.mocked(api.getProgress)).toHaveBeenCalledWith('book-1')
    // A book that was never opened claims nothing.
    expect(within(cardFor('The Shallows')).queryByRole('progressbar')).toBeNull()
  })

  it('shows no bar when the sentence total cannot be read', async () => {
    vi.mocked(api.getProgress).mockResolvedValue(30)
    vi.mocked(api.getSentences).mockRejectedValue(new TypeError('Failed to fetch'))

    await renderLibrary()

    await waitFor(() => expect(api.getSentences).toHaveBeenCalled())
    expect(screen.queryByRole('progressbar')).toBeNull()
    // The cards themselves are unaffected by a missing bar.
    expect(screen.getByText('Deep Work')).toBeTruthy()
  })

  it('shows no bar when the progress read fails', async () => {
    vi.mocked(api.getProgress).mockRejectedValue(new TypeError('Failed to fetch'))

    await renderLibrary()

    await waitFor(() => expect(api.getProgress).toHaveBeenCalled())
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('never asks for sentences of a book that has not been started', async () => {
    vi.mocked(api.getProgress).mockResolvedValue(0)

    await renderLibrary()

    await waitFor(() => expect(api.getProgress).toHaveBeenCalledTimes(BOOKS.length))
    expect(api.getSentences).not.toHaveBeenCalled()
  })

  it('resumes by opening the reader, which restores the saved sentence itself', async () => {
    vi.mocked(api.getProgress).mockImplementation(async (bookId: string) =>
      bookId === 'book-1' ? 30 : 0,
    )
    vi.mocked(api.getSentences).mockResolvedValue(sentences(120))

    await renderLibrary()
    await within(cardFor('Deep Work')).findByRole('progressbar')

    const { fireEvent } = await import('@testing-library/svelte')
    await fireEvent.click(within(cardFor('Deep Work')).getByRole('button', { name: 'Resume Deep Work' }))

    // No position travels in the URL: `loadBook` reads it back from the server.
    expect(vi.mocked(goto)).toHaveBeenCalledWith('/reader/book-1')
  })
})
