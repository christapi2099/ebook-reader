<script lang="ts">
  import { overlayLayer } from '$lib/actions/overlay-layer'
  import type { PageEntry } from '$lib/utils/page-index'

  /**
   * The skim overlay: every page of the book at a glance, each one a jump
   * target. Pages and their sentence targets come from the route, so this
   * component holds no notion of a page of its own.
   */
  let { pages, currentPage, bookTitle, onJump, onClose }: {
    pages: PageEntry[]
    /** 0-based, the same number the route's page indicator shows. */
    currentPage: number
    bookTitle: string
    onJump: (page: number) => void
    onClose: () => void
  } = $props()
</script>

<div
  class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
  role="dialog"
  aria-modal="true"
  aria-labelledby="pages-overlay-title"
  tabindex="-1"
  use:overlayLayer={{
    onClose,
    closeOnBackdrop: true,
    initialFocus: '[data-page-entry][aria-current="page"]',
  }}
>
  <div class="mx-4 flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl bg-surface shadow-3">
    <div class="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
      <div class="min-w-0">
        <h2 id="pages-overlay-title" class="text-lg font-bold text-fg">Pages</h2>
        <p class="truncate text-sm text-fg-muted">
          {bookTitle} · page {currentPage + 1} of {pages.length}
        </p>
      </div>
      <button
        type="button"
        class="min-h-11 shrink-0 rounded-lg border border-border px-4 text-sm text-fg hover:bg-surface-sunken"
        onclick={onClose}
      >
        Close
      </button>
    </div>

    {#if pages.length === 0}
      <p class="px-4 py-6 text-sm text-fg-muted">This book has no pages to skim yet.</p>
    {:else}
      <ul class="grid grid-cols-1 gap-1 overflow-auto p-3 sm:grid-cols-2">
        {#each pages as entry (entry.page)}
          <li>
            <button
              type="button"
              data-page-entry={entry.page}
              aria-current={entry.page === currentPage ? 'page' : undefined}
              class="flex min-h-11 w-full items-start gap-3 rounded-lg border px-3 py-2 text-left transition-colors {entry.page ===
              currentPage
                ? 'border-accent bg-accent-soft'
                : 'border-border hover:bg-surface-sunken'}"
              onclick={() => onJump(entry.page)}
            >
              <span class="w-8 shrink-0 pt-0.5 text-xs font-semibold tabular-nums text-fg-muted">
                {entry.page + 1}
              </span>
              <span class="line-clamp-2 min-w-0 flex-1 text-sm text-fg">{entry.preview}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</div>
