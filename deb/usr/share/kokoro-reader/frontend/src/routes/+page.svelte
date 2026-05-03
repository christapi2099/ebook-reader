<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { getLibrary } from '$lib/api'
  import type { Book } from '$lib/api'
  import LibraryCard from '$lib/components/LibraryCard.svelte'
  import UploadDialog from '$lib/components/UploadDialog.svelte'

  let books = $state<Book[]>([])
  let loading = $state(true)
  let uploadOpen = $state(false)
  let error = $state<string | null>(null)

  async function fetchLibrary() {
    loading = true
    error = null
    try {
      books = await getLibrary()
    } catch (e: any) {
      error = 'Could not connect to the backend. Make sure it is running on port 8000.'
    } finally {
      loading = false
    }
  }

  function handleUploaded(bookId: string, _title: string) {
    uploadOpen = false
    goto(`/reader/${bookId}`)
  }

  onMount(fetchLibrary)
</script>

<div class="p-4 md:p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-xl md:text-2xl font-bold text-slate-800">Library</h1>
    <button
      class="px-3 py-2 md:px-4 md:py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors text-sm md:text-base"
      onclick={() => (uploadOpen = true)}
    >
      + Add Book
    </button>
  </div>

  {#if loading}
    <div class="flex items-center gap-2 text-slate-500">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading library…
    </div>
  {:else if error}
    <div class="flex flex-col items-center justify-center py-16 text-center">
      <p class="text-red-500 font-medium">{error}</p>
      <button class="mt-3 px-4 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-sm text-slate-700 transition-colors" onclick={fetchLibrary}>
        Retry
      </button>
    </div>
  {:else if books.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-slate-400">
      <svg class="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
      </svg>
      <p class="text-lg font-medium">No books yet</p>
      <p class="text-sm mt-1">Upload a PDF or EPUB to get started</p>
      <button
        class="mt-4 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors"
        onclick={() => (uploadOpen = true)}
      >
        Upload Book
      </button>
    </div>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3 md:gap-4">
      {#each books as book (book.id)}
        <LibraryCard {book} onClick={(id) => goto(`/reader/${id}`)} />
      {/each}
    </div>
  {/if}
</div>

<UploadDialog open={uploadOpen} onClose={() => (uploadOpen = false)} onUploaded={handleUploaded} />
