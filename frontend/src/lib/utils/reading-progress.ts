/**
 * Reading progress for the library cards.
 *
 * The server stores a **sentence index** (`Progress.sentence_index`, through
 * `GET /library/{book_id}/progress`) and nothing else — there is no percentage,
 * no time estimate and no total in that contract (handoff §3.1). The percentage
 * here is therefore a *display* derivation from the index and the sentence total,
 * and is never persisted.
 *
 * Everything this module returns is absent when either half of the ratio is not
 * real. A card with no honest ratio shows no bar at all rather than a guess.
 */

/** What the library knows about one book's position. */
export interface StoredProgress {
  /** From `GET /library/{book_id}/progress`. */
  sentenceIndex: number
  /**
   * Sentence total, or `null` while it is unknown. `Book.page_count` is *not*
   * a substitute: it is a real page count for PDFs and a sentence count for
   * text, so using it here would compare two different units.
   */
  totalSentences: number | null
}

export interface ReadingProgress {
  /** The sentence the reader is parked on, clamped to the book. */
  sentenceIndex: number
  totalSentences: number
  /** 0–100 for the bar; derived at render time, never saved. */
  percent: number
  /** The last sentence is the end of the book, not "100%" (handoff §3.1). */
  finished: boolean
  /** Visible text: a percentage, or "Finished" on the last sentence. */
  label: string
}

/**
 * Derive what a card may show, or `null` when it must show no progress at all.
 *
 * `null` covers the two cases that carry no information:
 * - the total is unknown, so there is no honest denominator;
 * - the index is 0, which is what `GET /library/{book_id}/progress` returns both
 *   for "no progress row yet" and for "parked on the first sentence" — a card
 *   cannot tell them apart, so it claims neither.
 */
export function deriveReadingProgress(
  sentenceIndex: number | null | undefined,
  totalSentences: number | null | undefined,
): ReadingProgress | null {
  if (!isUsableNumber(sentenceIndex) || !isUsableNumber(totalSentences)) return null
  if (sentenceIndex <= 0 || totalSentences <= 0) return null

  const index = Math.min(sentenceIndex, totalSentences - 1)
  const finished = index === totalSentences - 1
  const percent = Math.round((index / totalSentences) * 100)

  return {
    sentenceIndex: index,
    totalSentences,
    percent,
    finished,
    label: finished ? 'Finished' : `${percent}%`,
  }
}

function isUsableNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}
