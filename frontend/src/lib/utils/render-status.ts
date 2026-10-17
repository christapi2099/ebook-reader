import type { Book } from '$lib/api'

/** What a library card shows for a book's render. */
export type RenderCardState = 'locked' | 'rerendering' | 'error' | 'ready'

export function isJobActive(book: Pick<Book, 'job_status'>): boolean {
  return book.job_status === 'pending' || book.job_status === 'processing'
}

export function hasReadyRender(book: Pick<Book, 'ready_generation'>): boolean {
  return book.ready_generation != null
}

/**
 * Locked: a job is active and there is nothing to read yet.
 * Re-rendering: a job is active but an older render is still openable.
 * Error: the last job failed and there is no render to fall back on... or even
 * with one, the card flags it so Retry is offered.
 */
export function renderCardState(book: Book): RenderCardState {
  if (isJobActive(book)) return hasReadyRender(book) ? 'rerendering' : 'locked'
  if (book.job_status === 'error') return 'error'
  return 'ready'
}

export function anyJobActive(books: Book[]): boolean {
  return books.some(isJobActive)
}

/** "about 3 min", "about 45 s"; `null` when unknown. */
export function formatEta(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null
  if (seconds < 60) return `about ${Math.max(1, Math.round(seconds))} s`
  if (seconds < 3600) return `about ${Math.round(seconds / 60)} min`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m ? `about ${h} h ${m} min` : `about ${h} h`
}
