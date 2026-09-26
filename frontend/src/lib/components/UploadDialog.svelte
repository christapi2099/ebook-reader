<script lang="ts">
  import { uploadDocument } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'
  import Dialog from '$lib/ui/Dialog.svelte'
  import Button from '$lib/ui/Button.svelte'

  let { open, onClose, onUploaded }: {
    open: boolean
    onClose: () => void
    onUploaded: (bookId: string, title: string) => void
  } = $props()

  const ACCEPTED_EXTENSIONS = ['pdf', 'epub']

  let file = $state<File | null>(null)
  let loading = $state(false)
  let errorMsg = $state('')

  /**
   * The one thing that can be checked before a round trip: the extension the
   * backend itself accepts (`routers/documents.py` rejects anything but pdf/epub
   * with a plain 400). Everything past that — DRM, missing text layers, size —
   * has no real check on either side, so it is not simulated here; the actual
   * failure surfaces from the upload response instead.
   */
  function validateFile(candidate: File): string | null {
    const ext = candidate.name.split('.').pop()?.toLowerCase()
    if (!ext || !ACCEPTED_EXTENSIONS.includes(ext)) {
      return 'Unsupported file type. Use PDF or EPUB.'
    }
    return null
  }

  function handleFileChange(e: Event) {
    const input = e.target as HTMLInputElement
    const picked = input.files?.[0] ?? null
    errorMsg = ''
    if (picked) {
      const validationError = validateFile(picked)
      if (validationError) {
        file = null
        errorMsg = validationError
        input.value = ''
        return
      }
    }
    file = picked
  }

  async function handleUpload() {
    if (!file || loading) return
    loading = true
    errorMsg = ''
    try {
      const data = await uploadDocument(file)
      onUploaded(data.book_id, file.name)
      onClose()
      file = null
    } catch (err) {
      errorMsg = toDetailMessage(err)
    } finally {
      loading = false
    }
  }
</script>

{#if open}
  <Dialog titleId="upload-dialog-title" {onClose}>
    <div class="relative mx-4 max-h-[85vh] w-full max-w-md overflow-y-auto rounded-xl bg-surface p-6 shadow-3">
      <button
        class="absolute right-3 top-3 text-xl leading-none text-fg-subtle transition-colors hover:text-fg"
        onclick={onClose}
        aria-label="Close upload dialog"
      >
        ×
      </button>

      <h2 id="upload-dialog-title" class="mb-4 text-xl font-bold text-fg">Upload Ebook</h2>

      <label
        class="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-border-strong p-8 text-center transition-colors hover:border-accent"
      >
        <svg class="mb-2 h-10 w-10 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        <span class="text-sm text-fg-muted">
          {file ? file.name : 'Click or drag PDF / EPUB here'}
        </span>
        {#if file}
          <span class="mt-1 text-xs text-fg-subtle">{(file.size / (1024 * 1024)).toFixed(1)} MB · {file.name.split('.').pop()?.toUpperCase()}</span>
        {/if}
        <input
          type="file"
          accept=".pdf,.epub"
          class="hidden"
          onchange={handleFileChange}
        />
      </label>

      {#if loading}
        <div class="mt-3 flex items-center gap-2 text-sm text-fg-muted" role="status">
          <svg class="h-4 w-4 flex-shrink-0 animate-spin text-accent" fill="none" viewBox="0 0 24 24" aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          Uploading and extracting text — this can take a moment for a long book.
        </div>
      {/if}

      {#if errorMsg}
        <div class="mt-3 flex items-start justify-between gap-3 rounded-lg border border-danger bg-danger-soft p-3" role="alert">
          <p class="text-sm text-danger">{errorMsg}</p>
          {#if file}
            <button
              type="button"
              class="min-h-11 flex-shrink-0 text-sm font-medium text-danger underline underline-offset-2"
              onclick={handleUpload}
            >
              Retry
            </button>
          {/if}
        </div>
      {/if}

      <Button class="mt-4 w-full" variant="primary" disabled={!file || loading} onclick={handleUpload}>
        {loading ? 'Uploading…' : 'Upload'}
      </Button>
    </div>
  </Dialog>
{/if}
