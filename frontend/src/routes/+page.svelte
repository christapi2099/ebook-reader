<script lang="ts">
  import { goto } from '$app/navigation'
  import { createTextBook } from '$lib/api'
  import UploadDialog from '$lib/components/UploadDialog.svelte'
  import Button from '$lib/ui/Button.svelte'

  let textarea = $state('')
  let isSubmitting = $state(false)
  let error = $state<string | null>(null)

  let uploadOpen = $state(false)

  async function handleSubmit() {
    if (!textarea.trim()) return

    isSubmitting = true
    error = null

    try {
      const result = await createTextBook(textarea.trim())
      // The book is created pending; the reader owns the render gate (the
      // processing screen, then audioStore.load once a generation is ready).
      await goto(`/reader/${result.book_id}`)
    } catch (e: any) {
      error = e?.message || 'Failed to create text book'
    } finally {
      isSubmitting = false
    }
  }

  function handleUploaded(bookId: string, _title: string) {
    uploadOpen = false
    goto(`/reader/${bookId}`)
  }
</script>

<!-- Text input; the reader takes over once the book is created -->
  <div class="flex flex-col h-full">
    <div class="flex-1 flex flex-col items-center justify-center p-4 md:p-8">
      <div class="w-full max-w-3xl space-y-6">
        <!-- Header with upload button -->
        <div class="flex items-center justify-between mb-4">
          <h1 class="text-2xl font-bold text-fg">Text Reader</h1>
          <Button onclick={() => uploadOpen = true} aria-label="Upload PDF/EPUB">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
            </svg>
            Upload
          </Button>
        </div>

        <!-- Text area -->
        <div class="space-y-3">
          <textarea
            bind:value={textarea}
            placeholder="Paste or type your text here..."
            class="w-full min-h-[200px] max-h-[50vh] p-4 border border-border-strong rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-transparent text-base leading-relaxed"
            disabled={isSubmitting}
          ></textarea>
        </div>

        {#if error}
          <div class="text-danger text-sm">{error}</div>
        {/if}

        <!-- Play button -->
        <div class="flex justify-center">
          <button
            onclick={handleSubmit}
            disabled={!textarea.trim() || isSubmitting}
            class="px-8 py-3 bg-accent text-accent-fg rounded-full text-lg font-medium hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {#if isSubmitting}
              <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
              </svg>
            {:else}
              <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z"/>
              </svg>
            {/if}
            Read Aloud
          </button>
        </div>
      </div>
    </div>
  </div>

<!-- Upload Dialog -->
<UploadDialog
  open={uploadOpen}
  onClose={() => uploadOpen = false}
  onUploaded={handleUploaded}
/>
