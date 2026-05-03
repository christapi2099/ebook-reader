<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { getLibrary, deleteBook, type Book } from '$lib/api'
  import BookGrid from '$lib/components/BookGrid.svelte'
  import LastRead from '$lib/components/LastRead.svelte'
  import { userStore } from '$lib/stores/user'

  let books = $state<Book[]>([])
  let loading = $state(true)
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

  async function handleDelete(bookId: string) {
    if (!confirm('Delete this book and all its data?')) return
    try {
      await deleteBook(bookId)
      books = await getLibrary()
    } catch (e: any) {
      error = e?.message ?? 'Failed to delete book'
    }
  }

  function handleResumeReading() {
    const state = $userStore
    if (state.settings.last_book_id) {
      goto(`/reader/${state.settings.last_book_id}`)
    }
  }

  onMount(async () => {
    // Load user settings first so LastRead card can check last_book_id
    await userStore.load()
    await fetchLibrary()
  })
</script>

<div class="p-4 md:p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-xl md:text-2xl font-bold text-slate-800">Library</h1>
  </div>

  <!-- Last read card at the top -->
  {#if $userStore.settings.last_book_id}
    <div class="mb-6">
      <LastRead
        bookId={$userStore.settings.last_book_id}
        sentenceIndex={$userStore.settings.last_sentence_index}
        onClick={handleResumeReading}
      />
    </div>
  {/if}

  <!-- Full book grid -->
  <BookGrid {books} {loading} {error} onClick={(id) => goto(`/reader/${id}`)} onDelete={handleDelete} onRetry={fetchLibrary} /></div>

