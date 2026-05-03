<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { getLibrary } from '$lib/api'
  import type { Book } from '$lib/api'
  import BookGrid from '$lib/components/BookGrid.svelte'
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

  <BookGrid {books} {loading} {error} onClick={(id) => goto(`/reader/${id}`)} onRetry={fetchLibrary} />
</div>

<UploadDialog open={uploadOpen} onClose={() => (uploadOpen = false)} onUploaded={handleUploaded} />
