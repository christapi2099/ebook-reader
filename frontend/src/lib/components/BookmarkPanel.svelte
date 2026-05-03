<script lang="ts">
  import { createBookmark, deleteBookmark, getBookmarks } from '$lib/api'
  import type { Bookmark } from '$lib/api'

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

<div class="fixed inset-0 bg-black/50 z-50 flex" onclick={onClose}>
  <div class="ml-auto w-80 max-w-[85vw] h-full bg-white shadow-xl flex flex-col" onclick={(e) => e.stopPropagation()}>
    <div class="flex items-center justify-between p-4 border-b border-slate-200">
      <h2 class="font-bold text-slate-800">Bookmarks</h2>
      <div class="flex items-center gap-2">
        <button
          class="p-1.5 rounded hover:bg-blue-50 text-blue-600 transition-colors"
          onclick={addBookmark}
          title="Add bookmark at current position"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21l-7-4-7 4V5a2 2 0 012-2h10a2 2 0 012 2v16z" />
          </svg>
        </button>
        <button class="p-1.5 rounded hover:bg-slate-100 text-slate-400 transition-colors" onclick={onClose}>
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-y-auto p-3">
      {#if error}
        <p class="text-sm text-red-500">{error}</p>
      {:else if loading}
        <div class="flex items-center justify-center py-8 text-slate-400">
          <svg class="w-4 h-4 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          Loading…
        </div>
      {:else if bookmarks.length === 0}
        <div class="flex flex-col items-center justify-center py-12 text-slate-400">
          <svg class="w-10 h-10 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 21l-7-4-7 4V5a2 2 0 012-2h10a2 2 0 012 2v16z" />
          </svg>
          <p class="text-sm font-medium">No bookmarks yet</p>
          <p class="text-xs mt-1">Tap the bookmark button to add one</p>
        </div>
      {:else}
        <div class="space-y-1">
          {#each bookmarks as bm (bm.id)}
            <div
              class="flex items-start justify-between gap-2 p-3 rounded-lg hover:bg-slate-50 transition-colors group cursor-pointer"
              onclick={() => goToBookmark(bm)}
            >
              <div class="min-w-0 flex-1">
                <p class="text-xs text-blue-500 font-medium">Page {bm.page}</p>
                <p class="text-sm text-slate-700 mt-0.5 line-clamp-2">{bm.label}</p>
                <p class="text-xs text-slate-400 mt-0.5">{formatDate(bm.created_at)}</p>
              </div>
              <button
                class="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-50 text-slate-400 hover:text-red-500 transition-all flex-shrink-0"
                onclick={(e) => { e.stopPropagation(); removeBookmark(bm.id) }}
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>
