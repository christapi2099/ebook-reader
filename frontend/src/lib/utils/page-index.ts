/**
 * Maps the reader's pages onto sentence indexes, so the page indicator and the
 * skim overlay can move the reading position with `seek()`.
 *
 * The page notion belongs to the route:
 * `totalPages = bookMeta?.page_count ?? reader.sentences.length` (handoff §5.4).
 * This module only answers "which sentence does page P start at", per format:
 *
 * - **Page-based (PDF):** every sentence carries its real page number, so a page
 *   starts at the first sentence whose `page` is at or after it. A page with no
 *   text of its own (a plate, a chapter opener) therefore lands on the next page
 *   that has some, rather than on an unrelated sentence.
 * - **Spread (everything else):** those books report `page = 0` on every
 *   sentence, so pages are distributed across the sentence range. When the page
 *   count *is* the sentence count (plain text) the ratio is 1 and page P starts
 *   exactly at sentence P; when it is not (EPUB page counts are derived from the
 *   sentence count) the pages still cover the whole book instead of its first
 *   slice.
 */
import type { Sentence } from '$lib/api'

export interface PageEntry {
  /** 0-based page number, the same number the route's `currentPage` holds. */
  page: number
  /** Index to pass to `seek()` to read this page. */
  sentenceIndex: number
  /** Text the page starts at, for the skim list. */
  preview: string
}

export function buildPageIndex(
  sentences: Sentence[],
  totalPages: number,
  pageBased: boolean,
): PageEntry[] {
  const pageCount = Number.isFinite(totalPages) ? Math.floor(totalPages) : 0
  if (sentences.length === 0 || pageCount <= 0) return []

  const entries: PageEntry[] = []
  // Sentences arrive in index order, so a page-based walk only ever moves forward.
  let cursor = 0

  for (let page = 0; page < pageCount; page++) {
    let position: number
    if (pageBased) {
      while (cursor < sentences.length - 1 && sentences[cursor].page < page) cursor++
      position = cursor
    } else {
      position = Math.min(
        sentences.length - 1,
        Math.floor((page * sentences.length) / pageCount),
      )
    }
    entries.push({
      page,
      sentenceIndex: sentences[position].index,
      preview: readableTextFrom(sentences, position),
    })
  }

  return entries
}

/**
 * The page a sentence sits on, or `null` when there are no pages to show yet.
 * Pages are ordered, so this is the last entry that starts at or before it.
 */
export function pageForSentenceIndex(
  entries: PageEntry[],
  sentenceIndex: number,
): number | null {
  if (entries.length === 0) return null

  let page = entries[0].page
  for (const entry of entries) {
    if (entry.sentenceIndex > sentenceIndex) break
    page = entry.page
  }
  return page
}

/**
 * The first sentence from `from` onwards that is worth reading. Pages can open
 * on a running head or a page number, which the extractor has already marked as
 * filtered; a preview of one of those would say nothing about the page.
 */
function readableTextFrom(sentences: Sentence[], from: number): string {
  for (let position = from; position < sentences.length; position++) {
    if (!sentences[position].filtered) return sentences[position].text
  }
  return sentences[from].text
}
