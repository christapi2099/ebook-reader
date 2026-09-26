import { describe, it, expect } from 'vitest'
import type { Sentence } from '$lib/api'
import { buildPageIndex, pageForSentenceIndex } from '$lib/utils/page-index'

/** Sentences default to page 0, exactly as the text/EPUB endpoints store them. */
function sentence(index: number, overrides: Partial<Sentence> = {}): Sentence {
  return {
    index,
    text: `Sentence ${index}`,
    page: 0,
    x0: 0,
    y0: 0,
    x1: 0,
    y1: 0,
    filtered: false,
    chapter: 0,
    ...overrides,
  }
}

function textBook(count: number) {
  return Array.from({ length: count }, (_, index) => sentence(index))
}

describe('buildPageIndex', () => {
  it('gives one sentence per page when the page count is the sentence count', () => {
    const entries = buildPageIndex(textBook(4), 4, false)

    expect(entries.map(e => [e.page, e.sentenceIndex])).toEqual([
      [0, 0],
      [1, 1],
      [2, 2],
      [3, 3],
    ])
    expect(entries[2].preview).toBe('Sentence 2')
  })

  it('spreads pages across the book when the page count is smaller', () => {
    // EPUB page counts are derived from the sentence count, so the pages must
    // still reach the end of the book rather than its first slice.
    const entries = buildPageIndex(textBook(40), 4, false)

    expect(entries.map(e => e.sentenceIndex)).toEqual([0, 10, 20, 30])
    expect(entries[3].preview).toBe('Sentence 30')
  })

  it('starts each PDF page at its first sentence', () => {
    const sentences = [
      sentence(0, { page: 0 }),
      sentence(1, { page: 0 }),
      sentence(2, { page: 1 }),
      sentence(3, { page: 2 }),
      sentence(4, { page: 2 }),
    ]

    const entries = buildPageIndex(sentences, 3, true)

    expect(entries.map(e => [e.page, e.sentenceIndex])).toEqual([
      [0, 0],
      [1, 2],
      [2, 3],
    ])
  })

  it('sends a PDF page with no sentences of its own to the next one that has text', () => {
    const sentences = [
      sentence(0, { page: 0 }),
      sentence(1, { page: 2 }),
      sentence(2, { page: 2 }),
    ]

    const entries = buildPageIndex(sentences, 3, true)

    expect(entries[1]).toMatchObject({ page: 1, sentenceIndex: 1 })
    expect(entries[2]).toMatchObject({ page: 2, sentenceIndex: 1 })
  })

  it('previews the first readable sentence, not a filtered running head', () => {
    const sentences = [
      sentence(0, { text: 'CHAPTER IV', filtered: true }),
      sentence(1, { text: 'The real opening line.' }),
    ]

    expect(buildPageIndex(sentences, 2, false)[0].preview).toBe('The real opening line.')
  })

  it('covers every page the route reports, and no more', () => {
    expect(buildPageIndex(textBook(10), 6, false)).toHaveLength(6)
  })

  it('has nothing to show before the sentences or the page count arrive', () => {
    expect(buildPageIndex([], 5, false)).toEqual([])
    expect(buildPageIndex(textBook(5), 0, false)).toEqual([])
  })
})

describe('pageForSentenceIndex', () => {
  it('finds the page a sentence sits on', () => {
    const entries = buildPageIndex(textBook(10), 5, false)

    expect(pageForSentenceIndex(entries, 0)).toBe(0)
    expect(pageForSentenceIndex(entries, 1)).toBe(0)
    expect(pageForSentenceIndex(entries, 2)).toBe(1)
    expect(pageForSentenceIndex(entries, 9)).toBe(4)
  })

  it('keeps an index past the last page on the last page', () => {
    const entries = buildPageIndex(textBook(10), 5, false)

    expect(pageForSentenceIndex(entries, 99)).toBe(4)
  })

  it('has no page before the index exists', () => {
    expect(pageForSentenceIndex([], 3)).toBeNull()
  })
})
