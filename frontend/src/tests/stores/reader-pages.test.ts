import { describe, it, expect, beforeEach, vi } from 'vitest'
import { get } from 'svelte/store'
import readerStore, { loadBook, seek, type ReaderState } from '$lib/stores/reader'
import { buildPageIndex, pageForSentenceIndex } from '$lib/utils/page-index'
import type { Sentence } from '$lib/api'
import * as api from '$lib/api'

vi.mock('$lib/api')

const BOOK_ID = 'book-1'
const SENTENCE_COUNT = 50

const SENTENCES: Sentence[] = Array.from({ length: SENTENCE_COUNT }, (_, index) => ({
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

const EMPTY: ReaderState = {
  bookId: null,
  sentences: [],
  currentIndex: 0,
  isPlaying: false,
  speed: 1,
}

/** The page index the reader route derives for this book. */
function pageIndex() {
  return buildPageIndex(SENTENCES, SENTENCE_COUNT, false)
}

describe('reader pages', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readerStore.set({ ...EMPTY })
    vi.mocked(api.getSentences).mockResolvedValue(SENTENCES)
    vi.mocked(api.getProgress).mockResolvedValue(0)
    vi.mocked(api.saveProgress).mockResolvedValue(undefined)
    vi.mocked(api.updateUserSettings).mockResolvedValue({ ok: true })
  })

  it('a page jump — seek then reload — lands on the same sentence', async () => {
    await loadBook(BOOK_ID)

    // Jumping to page 20 of 50 seeks to that page's sentence.
    const target = pageIndex()[20]
    await seek(target.sentenceIndex)
    expect(get(readerStore).currentIndex).toBe(20)

    // Reload: what the reader route does on mount, and what a browser refresh does.
    readerStore.set({ ...EMPTY })
    const saved = vi.mocked(api.saveProgress).mock.calls[0][1]
    vi.mocked(api.getProgress).mockResolvedValue(saved)
    await loadBook(BOOK_ID)

    const reloaded = get(readerStore)
    expect(reloaded.currentIndex).toBe(20)
    expect(pageForSentenceIndex(pageIndex(), reloaded.currentIndex)).toBe(target.page)
  })

  it('persists the sentence index, never a percentage', async () => {
    await loadBook(BOOK_ID)
    await seek(25)

    expect(api.saveProgress).toHaveBeenCalledWith(BOOK_ID, 25)
    expect(api.updateUserSettings).toHaveBeenCalledWith({
      last_book_id: BOOK_ID,
      last_sentence_index: 25,
    })
  })

  it('clamps a seek past the end, so no page can point off the book', async () => {
    await loadBook(BOOK_ID)

    await seek(SENTENCE_COUNT + 10)

    expect(get(readerStore).currentIndex).toBe(SENTENCE_COUNT - 1)
    expect(pageForSentenceIndex(pageIndex(), get(readerStore).currentIndex)).toBe(
      SENTENCE_COUNT - 1,
    )
  })
})
