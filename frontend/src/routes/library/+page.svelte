<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { getLibrary, deleteBook } from '$lib/api'
  import type { Book } from '$lib/api'
  import BookGrid from '$lib/components/BookGrid.svelte'

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

  onMount(fetchLibrary)
</script>

<div class="p-4 md:p-6">
  <h1 class="text-xl md:text-2xl font-bold text-slate-800 mb-6">Library</h1>
  <BookGrid {books} {loading} {error} onClick={(id) => goto(`/reader/${id}`)} onDelete={handleDelete} onRetry={fetchLibrary} />
</div>
