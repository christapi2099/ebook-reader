import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within, cleanup, fireEvent } from '@testing-library/svelte'
import LibraryPage from '../../routes/library/+page.svelte'
import type { Book } from '$lib/api'
import * as api from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))

// `getProgress` and `getSentences` are still mocked, but only so the tests can
// assert they are NEVER called. The route used to need one `getProgress` per book
// plus one `getSentences` per started book - the latter returning every sentence
// with word bounding boxes, to read a single integer. Both halves now arrive on
// the book itself from `GET /library`, so a regression that reintroduces those
// calls fails these tests.
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

const STARTED: Book = {
  id: 'book-1', title: 'Deep Work', author: null, file_type: 'pdf',
  page_count: 10, folder_id: null, sentence_count: 120, sentence_index: 30,
}

/** Never opened. `sentence_index` is null, which is NOT the same as 0. */
const UNSTARTED: Book = {
  id: 'book-2', title: 'The Shallows', author: null, file_type: 'text',
  page_count: 20, folder_id: null, sentence_count: 40, sentence_index: null,
}

const BOOKS: Book[] = [STARTED, UNSTARTED]

/** Render the library and wait for the cards to appear. */
async function renderLibrary() {
  render(LibraryPage)
  // Wait on the card's accessible name, not on its visible title text: once a card
  // renders a Resume button the title also appears in an `sr-only` span, so
  // `getByText` matches twice and fails as ambiguous.
  await waitFor(() => expect(screen.getByLabelText('Deep Work by Unknown')).toBeTruthy())
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
  })

  it('shows the position the server reported for each book', async () => {
    await renderLibrary()

    const bar = await within(cardFor('Deep Work')).findByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('25')
    // A book that was never opened claims nothing.
    expect(within(cardFor('The Shallows')).queryByRole('progressbar')).toBeNull()
  })

  it('shows no bar when the sentence total is unknown', async () => {
    // No denominator means no honest percentage, so there must be no bar at all -
    // a guessed one would be worse than none.
    vi.mocked(api.getLibrary).mockResolvedValue([
      { ...STARTED, sentence_count: undefined },
    ])

    await renderLibrary()

    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(cardFor('Deep Work')).toBeTruthy()
  })

  it('shows no bar when the position is unknown', async () => {
    // Note: this replaces a test that mocked `getProgress` rejecting. There is no
    // longer a separate progress read to fail - the position arrives with the
    // book - so the failure mode that can still occur is the server omitting it,
    // which is what this pins. A listing that fails outright is covered by the
    // error-and-Retry test.
    vi.mocked(api.getLibrary).mockResolvedValue([
      { ...STARTED, sentence_index: undefined },
    ])

    await renderLibrary()

    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(cardFor('Deep Work')).toBeTruthy()
  })

  it('shows no bar for a book parked on its first sentence', async () => {
    // 0 and null must not be conflated: 0 is a real position with a real
    // percentage, but it is not a meaningful one, so no bar is shown either way.
    vi.mocked(api.getLibrary).mockResolvedValue([{ ...STARTED, sentence_index: 0 }])

    await renderLibrary()

    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('asks for nothing beyond the library listing', async () => {
    await renderLibrary()

    await waitFor(() => expect(api.getLibrary).toHaveBeenCalledTimes(1))
    expect(api.getSentences).not.toHaveBeenCalled()
    expect(api.getProgress).not.toHaveBeenCalled()
  })

  it('resumes by opening the reader, which restores the saved sentence itself', async () => {
    await renderLibrary()
    await within(cardFor('Deep Work')).findByRole('progressbar')

    await fireEvent.click(within(cardFor('Deep Work')).getByRole('button', { name: 'Resume Deep Work' }))

    // No position travels in the URL: `loadBook` reads it back from the server.
    expect(vi.mocked(goto)).toHaveBeenCalledWith('/reader/book-1')
  })
})
