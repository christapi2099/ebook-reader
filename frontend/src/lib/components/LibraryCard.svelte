<script lang="ts">
  import { API_BASE, type Book } from '$lib/api'
  import { deriveReadingProgress } from '$lib/utils/reading-progress'
  import ProgressBar from '$lib/components/ui/ProgressBar.svelte'
  import { renderCardState, formatEta } from '$lib/utils/render-status'

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
    onRetry,
    onDelete,
    onMove,
    onEditMetadata,
    onDragStart,
    onDragEnd,
  }: {
    book: Book
    /** From `GET /library/{book_id}/progress`; `null` while unknown. */
    sentenceIndex?: number | null
    /** Sentence total, or `null` when it is not known — no bar is shown then. */
    totalSentences?: number | null
    onClick: (id: string) => void
    /** Re-queues a failed render. */
    onRetry?: (id: string) => void
    onDelete?: (id: string) => void
    /** Opens the move dialog — the keyboard-reachable way to file a book. */
    onMove?: (book: Book) => void
    /** Opens the title/author dialog. */
    onEditMetadata?: (book: Book) => void
    /** Enables dragging the card as a drop payload for folder tiles. */
    onDragStart?: (book: Book) => void
    onDragEnd?: () => void
  } = $props()

  let showMenu = $state(false)
  let dragging = $state(false)
  let coverLoaded = $state(false)

  const renderState = $derived(renderCardState(book))
  const locked = $derived(renderState === 'locked')
  const eta = $derived(formatEta(book.eta_seconds))
  const percent = $derived(Math.round(Math.min(1, Math.max(0, book.progress ?? 0)) * 100))

  const progress = $derived(deriveReadingProgress(sentenceIndex, totalSentences))

  // Cover art: the mockup's own rules reserve the vivid gradient palettes for
  // books that actually have art (styles.css `.cover-a`..`.cover-f`). Every
  // book here has none, and the mockup's own answer for that case is
  // `.book-cover.art-missing` — a dashed border on a muted surface, not a
  // gradient (handoff §3.5 also rules out shipping the gradients as if they
  // were real jackets). That's what this renders.

  /**
   * What the number under the title counts.
   *
   * `page_count` is only pages for a PDF: for a text book it is a sentence
   * count and for an EPUB it is a derived `len(raw) // 10`, so labelling either
   * of those "pages" states something untrue. The sentence total is the one
   * number that means the same thing for every non-PDF format, and `GET
   * /library` already sends it. `file_type` is the extension the upload stored,
   * always lowercase — `routers/documents.py` lowercases it — and this is the
   * same strict comparison the reader route makes to decide what a "page" is.
   *
   * A count of 0 or a missing one says nothing at all — the reader has no
   * sentences to report, and "0 sentences" would read as a fact about the book
   * rather than an absence of one.
   */
  const unitLabel = $derived.by(() => {
    if (book.file_type === 'pdf') return `${book.page_count} pages`
    const sentences = book.sentence_count ?? 0
    return sentences > 0 ? `${sentences} sentences` : null
  })

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
  class="bg-surface rounded-xl shadow-1 border border-border p-4 hover:shadow-2 hover:-translate-y-0.5 transition-[box-shadow,transform] relative group cursor-pointer {dragging ? 'opacity-50' : ''}"
  role="button"
  tabindex="0"
  draggable={Boolean(onDragStart)}
  aria-label={`${book.title} by ${book.author ?? 'Unknown'}`}
  aria-disabled={locked}
  data-render-state={renderState}
  onclick={() => { if (!locked) onClick(book.id) }}
  onkeydown={(e) => {
    // Only the card itself opens the book. Without this guard, Enter on the
    // "Book options" button bubbles here and navigates away instead of
    // opening the menu, which would make the move dialog unreachable by
    // keyboard.
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!locked) onClick(book.id)
    }
  }}
  ondragstart={handleDragStart}
  ondragend={handleDragEnd}
>
  {#if onDelete || onMove || onEditMetadata}
    <!--
      The three raw z-values in here are local stacking inside one card — the
      options button above the cover, a click-catcher under the menu, the menu
      above the catcher. The named scale has no layer for any of them (a menu
      would need a slot between `overlay` 40 and `dialog` 50, which does not
      exist), so they keep their numbers rather than borrow a name they do not
      mean. See the z-index bucket list in the sweep report.
    -->
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
            {#if onEditMetadata}
              <button
                class="w-full min-h-11 px-3 text-left text-sm text-fg hover:bg-surface-sunken flex items-center gap-2"
                onclick={() => { onEditMetadata(book); showMenu = false }}
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
                </svg>
                Edit book details
              </button>
            {/if}
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
  <!--
    Dashed border + muted surface, per the mockup's own `.book-cover.art-missing`
    rule for a book with no derivable art (handoff §3.5 rules out the vivid
    gradients here — those are reserved for books that actually have a cover).
    Title/author stay text-only below the cover, their one accessible spot.
  -->
  <div
    class="relative aspect-[2/3] rounded-lg overflow-hidden border border-dashed border-border-strong bg-surface-sunken flex flex-col items-center justify-center gap-2 p-4 transition-transform duration-200 group-hover:-translate-y-1 group-hover:shadow-2"
    data-cover-placeholder={book.id}
  >
    {#if book.file_type === 'pdf'}
      <img
        src={`${API_BASE}/library/${encodeURIComponent(book.id)}/cover`}
        alt=""
        class="absolute inset-0 h-full w-full object-cover {coverLoaded ? '' : 'hidden'}"
        onload={() => (coverLoaded = true)}
        onerror={() => (coverLoaded = false)}
        data-cover-image
      />
    {/if}
    <span class="absolute top-2 left-2 rounded-full bg-surface/90 backdrop-blur-sm border border-border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-fg-muted">
      {book.file_type}
    </span>
    <svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
    </svg>
    <span class="text-xs text-fg-subtle">No cover art</span>

    {#if renderState === 'rerendering'}
      <span class="absolute top-2 right-12 rounded-full bg-accent-soft border border-accent px-2 py-0.5 text-[9px] font-bold text-accent" data-rerendering-badge>
        Re-rendering…
      </span>
    {/if}

    {#if !locked && progress}
      <div
        class="absolute inset-x-2 bottom-1 h-1 overflow-hidden rounded-full bg-border"
        role="progressbar"
        aria-label={`Reading progress for ${book.title}`}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={progress.percent}
        aria-valuetext={progress.label}
      >
        <div class="h-full rounded-full bg-accent" style={`width: ${progress.percent}%`}></div>
      </div>
    {/if}

    {#if !locked && progress}
      <button
        type="button"
        class="absolute bottom-3 z-10 min-h-11 rounded-full border border-border bg-surface px-3 text-xs font-semibold text-accent shadow-1 hover:bg-accent-soft"
        onclick={handleResume}
      >
        Resume
        <span class="sr-only"> {book.title}</span>
      </button>
    {/if}
  </div>

  {#if locked}
    <div class="mt-2 text-xs text-fg-muted" role="status" data-locked-status>
      <ProgressBar
        size="sm"
        percent={book.job_status === 'pending' ? null : percent}
        label={`Processing ${book.title}`}
      />
      <p class="mt-1 tabular-nums">
        {book.job_status === 'pending' ? 'Queued' : `Processing ${percent}%`}
        {#if book.job_status === 'pending' && book.queue_position != null} · #{book.queue_position} in queue{/if}
        {#if eta} · {eta}{/if}
      </p>
    </div>
  {:else if renderState === 'error'}
    <div class="mt-2 text-xs" data-error-status>
      <p class="text-danger">Processing failed</p>
      {#if onRetry}
        <button
          type="button"
          class="mt-1 min-h-11 rounded-md border border-border px-3 text-xs font-semibold text-accent hover:bg-accent-soft"
          onclick={(e) => { e.stopPropagation(); onRetry(book.id) }}
        >
          Retry<span class="sr-only"> {book.title}</span>
        </button>
      {/if}
    </div>
  {/if}

  <h3 class="font-semibold text-fg mt-2 line-clamp-2 text-sm">{book.title}</h3>
  <!--
    No import can read an author out of the file, so every book starts without
    one until "Edit book details" is used — this is the usual state, not a
    failure.
  -->
  <p class="text-sm text-fg-muted mt-1">{book.author ?? 'Author not detected'}</p>
  {#if unitLabel}
    <p class="text-xs text-fg-subtle mt-1">{unitLabel}</p>
  {/if}
  {#if progress}
    <p class="mt-1 text-xs text-fg-muted tabular-nums">{progress.label}</p>
  {/if}
</div>
