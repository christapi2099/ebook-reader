<script lang="ts">
  import { uploadDocument } from '$lib/api'

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
  <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
    <div class="bg-white rounded-xl p-6 max-w-md max-h-[85vh] overflow-y-auto w-full shadow-xl relative mx-4">
      <button
        class="absolute top-3 right-3 text-slate-400 hover:text-slate-700 text-xl leading-none"
        onclick={onClose}
        aria-label="Close"
      >
        ×
      </button>

      <h2 class="text-xl font-bold text-slate-800 mb-4">Upload Ebook</h2>

      <label
        class="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 rounded-lg p-8 text-center cursor-pointer hover:border-blue-400 transition-colors"
      >
        <svg class="w-10 h-10 text-slate-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        <span class="text-sm text-slate-500">
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
        <p class="mt-3 text-sm text-red-500">{errorMsg}</p>
      {/if}

      <button
        disabled={!file || loading}
        class="mt-4 w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium transition-colors"
        onclick={handleUpload}
      >
        {loading ? 'Uploading…' : 'Upload'}
      </button>
    </div>
  </div>
{/if}
