<script lang="ts">
  import LibraryCard from '$lib/components/LibraryCard.svelte'
  import Button from '$lib/ui/Button.svelte'
  import type { Book } from '$lib/api'
  import type { StoredProgress } from '$lib/utils/reading-progress'

  let { books, loading, error, progress = {}, onRetry, onClick, onDelete, onMove, onEditMetadata, onDragStart, onDragEnd }: {
    books: Book[]
    loading: boolean
    error: string | null
    /**
     * Real reading positions by book id, from the server. A book with no entry
     * (or an unknown sentence total) shows no progress rather than a guess.
     */
    progress?: Record<string, StoredProgress>
    onRetry: () => void
    onClick: (id: string) => void
    onDelete?: (id: string) => void
    onMove?: (book: Book) => void
    /** Opens the title/author dialog for the book. */
    onEditMetadata?: (book: Book) => void
    onDragStart?: (book: Book) => void
    onDragEnd?: () => void
  } = $props()
</script>

{#if loading}
  <div class="flex items-center gap-2 text-fg-muted">
    <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
    </svg>
    Loading library…
  </div>
{:else if error}
  <div class="flex flex-col items-center justify-center py-16 text-center">
    <p class="text-danger font-medium">{error}</p>
    <Button variant="secondary" class="mt-3" onclick={onRetry}>Retry</Button>
  </div>
{:else if books.length === 0}
  <div class="flex flex-col items-center justify-center py-20 text-fg-subtle">
    <svg class="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
        d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
    </svg>
    <p class="text-lg font-medium">No books yet</p>
    <p class="text-sm mt-1">Upload a PDF or EPUB to get started</p>
  </div>
{:else}
  <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3 md:gap-4">
    {#each books as book (book.id)}
      <LibraryCard
        {book}
        sentenceIndex={progress[book.id]?.sentenceIndex ?? null}
        totalSentences={progress[book.id]?.totalSentences ?? null}
        {onClick}
        {onDelete}
        {onMove}
        {onEditMetadata}
        {onDragStart}
        {onDragEnd}
      />
    {/each}
  </div>
{/if}
