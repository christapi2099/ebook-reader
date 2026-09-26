<script lang="ts">
  import { uploadDocument } from '$lib/api'
  import Dialog from '$lib/ui/Dialog.svelte'
  import Button from '$lib/ui/Button.svelte'

  let { open, onClose, onUploaded }: {
    open: boolean
    onClose: () => void
    onUploaded: (bookId: string, title: string) => void
  } = $props()

  let file = $state<File | null>(null)
  let loading = $state(false)
  let errorMsg = $state('')

  function handleFileChange(e: Event) {
    const input = e.target as HTMLInputElement
    file = input.files?.[0] ?? null
    errorMsg = ''
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
    } catch (err: any) {
      errorMsg = err?.message ?? 'Upload failed'
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
        <input
          type="file"
          accept=".pdf,.epub"
          class="hidden"
          onchange={handleFileChange}
        />
      </label>

      {#if errorMsg}
        <p class="mt-3 text-sm text-danger">{errorMsg}</p>
      {/if}

      <Button class="mt-4 w-full" variant="primary" disabled={!file || loading} onclick={handleUpload}>
        {loading ? 'Uploading…' : 'Upload'}
      </Button>
    </div>
  </Dialog>
{/if}
