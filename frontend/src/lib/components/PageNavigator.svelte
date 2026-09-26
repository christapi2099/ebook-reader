<script lang="ts">
  /**
   * The page indicator: current page, the total, and a way to type a page.
   *
   * Pages are the route's notion of them — for PDFs the real pages, otherwise
   * one page per sentence (`totalPages = bookMeta?.page_count ?? sentences.length`).
   * `onGoToPage` receives a 0-based page; the route turns it into a `seek()`.
   */
  let {
    currentPage = 0,
    totalPages = 1,
    onGoToPage,
  }: {
    currentPage?: number
    totalPages?: number
    onGoToPage: (page: number) => void
  } = $props()

  let draftValue = $state(String(currentPage + 1))

  $effect(() => { draftValue = String(currentPage + 1) })

  function handleInput(e: Event) {
    const v = parseInt((e.target as HTMLInputElement).value, 10)
    if (!isNaN(v)) onGoToPage(Math.max(0, Math.min(v - 1, totalPages - 1)))
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') handleInput(e)
  }
</script>

<div class="flex items-center gap-1 text-sm text-fg">
  <button
    type="button"
    onclick={() => onGoToPage(Math.max(0, currentPage - 1))}
    disabled={currentPage === 0}
    class="flex h-11 w-11 items-center justify-center rounded-md hover:bg-surface-sunken disabled:opacity-40"
    aria-label="Previous page"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
    </svg>
  </button>
  <input
    type="number"
    bind:value={draftValue}
    onblur={handleInput}
    onkeydown={handleKeydown}
    onclick={(e) => (e.target as HTMLInputElement).select()}
    class="h-11 w-14 rounded border border-border-strong bg-surface px-1 text-center tabular-nums focus:outline-none focus:ring-2 focus:ring-focus-ring"
    min="1"
    max={totalPages}
    aria-label="Page number"
  />
  <span class="text-fg-muted tabular-nums">/ {totalPages}</span>
  <button
    type="button"
    onclick={() => onGoToPage(Math.min(totalPages - 1, currentPage + 1))}
    disabled={currentPage === totalPages - 1}
    class="flex h-11 w-11 items-center justify-center rounded-md hover:bg-surface-sunken disabled:opacity-40"
    aria-label="Next page"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
    </svg>
  </button>
</div>
