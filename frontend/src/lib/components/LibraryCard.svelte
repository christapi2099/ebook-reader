<script lang="ts">
  import type { Book } from '$lib/api'
  import { deriveReadingProgress } from '$lib/utils/reading-progress'

  /**
   * One book in the library.
   *
   * The card shows what the server actually knows: the real reading position
   * (when there is one), the real file type, and a placeholder standing in for
   * cover art, which the app cannot derive yet (handoff §3.5 — the prototype's
   * typographic covers must not ship as if they were artwork).
   */
  let {
    book,
    sentenceIndex = null,
    totalSentences = null,
    onClick,
    onDelete,
    onMove,
    onDragStart,
    onDragEnd,
  }: {
    book: Book
    /** From `GET /library/{book_id}/progress`; `null` while unknown. */
    sentenceIndex?: number | null
    /** Sentence total, or `null` when it is not known — no bar is shown then. */
    totalSentences?: number | null
    onClick: (id: string) => void
    onDelete?: (id: string) => void
    /** Opens the move dialog — the keyboard-reachable way to file a book. */
    onMove?: (book: Book) => void
    /** Enables dragging the card as a drop payload for folder tiles. */
    onDragStart?: (book: Book) => void
    onDragEnd?: () => void
  } = $props()

  let showMenu = $state(false)
  let dragging = $state(false)

  const progress = $derived(deriveReadingProgress(sentenceIndex, totalSentences))

  function handleDragStart(event: DragEvent) {
    if (!onDragStart) return
    // `text/plain` keeps the payload readable by the folder tile's drop handler.
    event.dataTransfer?.setData('text/plain', book.id)
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
    dragging = true
    onDragStart(book)
  }

  function handleDragEnd() {
    dragging = false
    onDragEnd?.()
  }

  /**
   * Resuming is opening the book: `loadBook` restores the saved sentence from
   * the server (handoff §3.1), so there is no position to pass along and no
   * second source of truth for it. The event is stopped because the card itself
   * is also clickable.
   */
  function handleResume(event: MouseEvent) {
    event.stopPropagation()
    onClick(book.id)
  }
</script>

<div
  class="bg-surface rounded-xl shadow-1 border border-border p-4 hover:shadow-2 transition-shadow relative group cursor-pointer {dragging ? 'opacity-50' : ''}"
  role="button"
  tabindex="0"
  draggable={Boolean(onDragStart)}
  aria-label={`${book.title} by ${book.author ?? 'Unknown'}`}
  onclick={() => onClick(book.id)}
  onkeydown={(e) => {
    // Only the card itself opens the book. Without this guard, Enter on the
    // "Book options" button bubbles here and navigates away instead of
    // opening the menu, which would make the move dialog unreachable by
    // keyboard.
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onClick(book.id)
    }
  }}
  ondragstart={handleDragStart}
  ondragend={handleDragEnd}
>
  {#if onDelete || onMove}
    <div class="absolute top-2 right-2 z-10" role="button" tabindex="-1" onclick={(e) => e.stopPropagation()}>
      <button
        class="w-11 h-11 rounded-full flex items-center justify-center text-fg-subtle hover:bg-surface-sunken hover:text-fg transition-colors"
        onclick={() => (showMenu = !showMenu)}
        aria-label={`Book options for ${book.title}`}
        aria-expanded={showMenu}
        data-book-options={book.id}
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      </button>
      {#if showMenu}
        <div class="relative">
          <div class="fixed inset-0 z-10" role="button" tabindex="-1" onclick={() => (showMenu = false)}></div>
          <div class="absolute right-0 top-12 bg-surface-raised border border-border rounded-lg shadow-2 z-20 py-1 min-w-[10rem]">
            {#if onMove}
              <button
                class="w-full min-h-11 px-3 text-left text-sm text-fg hover:bg-surface-sunken flex items-center gap-2"
                onclick={() => { onMove(book); showMenu = false }}
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                </svg>
                Move to folder
              </button>
            {/if}
            {#if onDelete}
              <button
                class="w-full min-h-11 px-3 text-left text-sm text-danger hover:bg-danger-soft flex items-center gap-2"
                onclick={() => { onDelete(book.id); showMenu = false }}
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
                Delete
              </button>
            {/if}
          </div>
        </div>
      {/if}
    </div>
  {/if}
  <!-- No book can have cover art yet, so this placeholder is the normal look. -->
  <div
    class="relative h-32 rounded-lg bg-surface-sunken border border-border flex flex-col items-center justify-center gap-1"
    data-cover-placeholder={book.id}
  >
    <svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
    </svg>
    <span class="text-xs text-fg-subtle">No cover</span>
    <span class="absolute top-2 left-2 text-xs bg-surface border border-border text-fg-muted rounded px-1.5 py-0.5 font-medium uppercase">
      {book.file_type}
    </span>
  </div>
  <h3 class="font-semibold text-fg mt-2 line-clamp-2 text-sm">{book.title}</h3>
  <!-- `Book.author` is never set by any endpoint, so this is the usual state. -->
  <p class="text-sm text-fg-muted mt-1">{book.author ?? 'Author not detected'}</p>
  <p class="text-xs text-fg-subtle mt-1">{book.page_count} pages</p>

  {#if progress}
    <div class="mt-2">
      <div
        class="h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken"
        role="progressbar"
        aria-label={`Reading progress for ${book.title}`}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={progress.percent}
        aria-valuetext={progress.label}
      >
        <div class="h-full rounded-full bg-accent" style={`width: ${progress.percent}%`}></div>
      </div>
      <p class="mt-1 text-xs text-fg-muted tabular-nums">{progress.label}</p>
    </div>
    <button
      type="button"
      class="mt-2 min-h-11 w-full rounded-lg border border-border px-3 text-sm font-medium text-accent hover:bg-accent-soft"
      onclick={handleResume}
    >
      Resume
      <span class="sr-only"> {book.title}</span>
    </button>
  {/if}
</div>
