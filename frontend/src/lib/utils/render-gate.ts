import type { Book } from '$lib/api'

export const PROCESSING_POLL_MS = 3000
export const READY_POLL_MS = 10_000

export interface RenderGateOptions {
  bookId: string
  getBook: (id: string) => Promise<Book>
  /** The generation currently loaded into the reader, or `null` for none. */
  loadedGeneration: number | null
  /** Called with every fresh Book, whatever it says. */
  onBook?: (book: Book) => void
  /** No generation was loaded and one is now ready. */
  onReady: (generation: number, book: Book) => void
  /** A generation newer than the loaded one has been published. */
  onNewer: (generation: number, book: Book) => void
}

/**
 * Watches a book's render status for the reader.
 *
 * Without a loaded generation it polls fast (the processing screen); with one
 * it polls every 10 s and on window focus, looking for a newer generation.
 * Network errors are swallowed: a missed tick is retried by the next one.
 */
export function createRenderGate(opts: RenderGateOptions) {
  let loaded = opts.loadedGeneration
  let timer: ReturnType<typeof setTimeout> | null = null
  let running = false
  let inFlight = false

  async function check() {
    if (inFlight) return
    inFlight = true
    try {
      const book = await opts.getBook(opts.bookId)
      if (!running) return
      opts.onBook?.(book)
      const ready = book.ready_generation ?? null
      if (ready !== null && (loaded === null || ready > loaded)) {
        if (loaded === null) opts.onReady(ready, book)
        else opts.onNewer(ready, book)
      }
    } catch {
      // Retry on the next tick.
    } finally {
      inFlight = false
    }
  }

  function schedule() {
    if (!running) return
    timer = setTimeout(async () => {
      await check()
      schedule()
    }, loaded === null ? PROCESSING_POLL_MS : READY_POLL_MS)
  }

  function onFocus() {
    void check()
  }

  return {
    start() {
      if (running) return
      running = true
      window.addEventListener('focus', onFocus)
      void check()
      schedule()
    },
    stop() {
      running = false
      if (timer) clearTimeout(timer)
      timer = null
      window.removeEventListener('focus', onFocus)
    },
    /** Record the generation now on screen; restarts the cadence. */
    setLoaded(generation: number | null) {
      loaded = generation
      if (running) {
        if (timer) clearTimeout(timer)
        schedule()
      }
    },
  }
}
