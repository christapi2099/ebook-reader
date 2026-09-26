<script lang="ts">
  import { createBookmark, deleteBookmark, getBookmarks } from '$lib/api'
  import type { Bookmark } from '$lib/api'
  import Dialog from '$lib/ui/Dialog.svelte'

  let {
    bookId,
    currentSentenceIndex,
    currentSentenceText,
    onGoToSentence,
    onClose,
  }: {
    bookId: string
    currentSentenceIndex: number
    currentSentenceText: string
    onGoToSentence: (index: number) => void
    onClose: () => void
  } = $props()

  let bookmarks = $state<Bookmark[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function fetchBookmarks() {
    loading = true
    error = null
    try {
      bookmarks = await getBookmarks(bookId)
    } catch (e: any) {
      error = e?.message ?? 'Failed to load bookmarks'
    } finally {
      loading = false
    }
  }

  async function addBookmark() {
    const label = currentSentenceText.slice(0, 50) || `Sentence ${currentSentenceIndex}`
    try {
      await createBookmark(bookId, currentSentenceIndex, label)
      await fetchBookmarks()
    } catch (e: any) {
      error = e?.message ?? 'Failed to add bookmark'
    }
  }

  async function removeBookmark(id: number) {
    try {
      await deleteBookmark(id)
      bookmarks = bookmarks.filter(b => b.id !== id)
    } catch (e: any) {
      error = e?.message ?? 'Failed to delete bookmark'
    }
  }

  function goToBookmark(b: Bookmark) {
    onGoToSentence(b.sentence_index)
    onClose()
  }

  function formatDate(dateStr: string): string {
    return new Date(dateStr).toLocaleDateString()
  }

  fetchBookmarks()
</script>

<Dialog titleId="bookmarks-panel-title" {onClose} layout="stretch" closeOnBackdrop>
  <div class="ml-auto flex h-full w-80 max-w-[85vw] flex-col bg-surface shadow-3">
    <div class="flex items-center justify-between border-b border-border p-4">
      <h2 id="bookmarks-panel-title" class="font-bold text-fg">Bookmarks</h2>
      <div class="flex items-center gap-2">
        <button
          class="rounded p-1.5 text-accent transition-colors hover:bg-accent-soft"
          onclick={addBookmark}
          aria-label="Add bookmark at current position"
        >
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21l-7-4-7 4V5a2 2 0 012-2h10a2 2 0 012 2v16z" />
          </svg>
        </button>
        <button
          class="rounded p-1.5 text-fg-subtle transition-colors hover:bg-surface-sunken"
          onclick={onClose}
          aria-label="Close bookmarks"
        >
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-y-auto p-3">
      {#if error}
        <p class="text-sm text-danger">{error}</p>
      {:else if loading}
        <div class="flex items-center justify-center py-8 text-fg-subtle">
          <svg class="mr-2 h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          Loading…
        </div>
      {:else if bookmarks.length === 0}
        <div class="flex flex-col items-center justify-center py-12 text-fg-subtle">
          <svg class="mb-2 h-10 w-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 21l-7-4-7 4V5a2 2 0 012-2h10a2 2 0 012 2v16z" />
          </svg>
          <p class="text-sm font-medium">No bookmarks yet</p>
          <p class="mt-1 text-xs">Tap the bookmark button to add one</p>
        </div>
      {:else}
        <div class="space-y-1">
          {#each bookmarks as bm (bm.id)}
            <div
              class="group flex cursor-pointer items-start justify-between gap-2 rounded-lg p-3 transition-colors hover:bg-surface-sunken"
              onclick={() => goToBookmark(bm)}
            >
              <div class="min-w-0 flex-1">
                <p class="text-xs font-medium text-accent">Page {bm.page}</p>
                <p class="mt-0.5 line-clamp-2 text-sm text-fg">{bm.label}</p>
                <p class="mt-0.5 text-xs text-fg-subtle">{formatDate(bm.created_at)}</p>
              </div>
              <button
                class="flex-shrink-0 rounded p-1 text-fg-subtle opacity-0 transition-all group-hover:opacity-100 hover:bg-danger-soft hover:text-danger"
                onclick={(e) => { e.stopPropagation(); removeBookmark(bm.id) }}
                aria-label="Delete bookmark"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</Dialog>
