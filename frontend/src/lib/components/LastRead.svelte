<script lang="ts">
  import { onMount } from 'svelte'
  import { getLibrary } from '$lib/api'
  import type { Book } from '$lib/api'

  let { bookId, sentenceIndex, onClick }: {
    bookId: string | null
    sentenceIndex: number
    onClick: () => void
  } = $props()

  let book = $state<Book | null>(null)
  let loading = $state(true)
  let error = $state<string | null>(null)
  let pageNumber = $derived(book?.page_count ?? 1)

  async function loadBookDetails() {
    if (!bookId) {
      loading = false
      return
    }

    loading = true
    error = null
    try {
      const books = await getLibrary()
      const found = books.find((b) => b.id === bookId)
      book = found || null
      loading = false
    } catch (e: any) {
      error = 'Could not load book details'
      loading = false
    }
  }

  onMount(loadBookDetails)
</script>

{#if bookId && book && !loading}
  <div
    class="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-xl border border-blue-200 p-5 cursor-pointer hover:shadow-md transition-all"
    role="button"
    tabindex="0"
    onclick={onClick}
    onkeydown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onClick()
      }
    }}
  >
    <div class="flex items-start gap-4">
      <div class="flex-shrink-0 w-12 h-12 bg-blue-500 rounded-lg flex items-center justify-center">
        <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
        </svg>
      </div>

      <div class="flex-grow min-w-0">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-xs font-semibold text-blue-600 uppercase tracking-wide">
            Resume Reading
          </span>
          <span class="text-xs text-slate-500">•</span>
          <span class="text-xs text-slate-500">Page {pageNumber}</span>
        </div>
        <h2 class="font-semibold text-slate-800 text-lg leading-tight line-clamp-2">
          {book.title}
        </h2>
        {#if book.author}
          <p class="text-sm text-slate-600 mt-1">{book.author}</p>
        {/if}
      </div>

      <div class="flex-shrink-0 self-center">
        <div class="w-10 h-10 bg-white rounded-full flex items-center justify-center shadow-sm border border-blue-100">
          <svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
    </div>
  </div>
{/if}
