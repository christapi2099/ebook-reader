<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { getExports, exportMP3, deleteExport, getVoices, API_BASE } from '$lib/api'
  import type { Book, Voice, ExportItem } from '$lib/api'
  import { getLibrary } from '$lib/api'

  let exports = $state<ExportItem[]>([])
  let books = $state<Book[]>([])
  let voices = $state<Voice[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let showDialog = $state(false)
  let selectedBookId = $state('')
  let selectedVoice = $state('af_heart')
  let selectedSpeed = $state(1.0)
  let exporting = $state(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

  async function fetchExports() {
    loading = true
    error = null
    try {
      const [ex, bk, vo] = await Promise.all([getExports(), getLibrary(), getVoices()])
      exports = ex
      books = bk
      voices = vo
    } catch (e: any) {
      error = 'Could not load exports. Make sure the backend is running.'
    } finally {
      loading = false
    }
  }

  async function pollExports() {
    const hasActive = exports.some(e => e.status === 'pending' || e.status === 'processing')
    if (!hasActive) return
    try {
      exports = await getExports()
    } catch {
      // ignore poll errors
    }
  }

  async function handleExport() {
    if (!selectedBookId || exporting) return
    exporting = true
    try {
      await exportMP3(selectedBookId, selectedVoice, selectedSpeed)
      showDialog = false
      await fetchExports()
    } catch (e: any) {
      error = e?.message ?? 'Export failed'
    } finally {
      exporting = false
    }
  }

  async function handleDeleteExport(exportId: number) {
    try {
      await deleteExport(exportId)
      exports = exports.filter(e => e.id !== exportId)
    } catch (e: any) {
      error = e?.message ?? 'Delete failed'
    }
  }

  function formatSize(bytes: number | null): string {
    if (!bytes) return '—'
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  function formatDate(dateStr: string): string {
    return new Date(dateStr).toLocaleDateString()
  }

  onMount(() => {
    fetchExports()
    pollTimer = setInterval(pollExports, 3000)
  })

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
  })
</script>

<div class="p-4 md:p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-xl md:text-2xl font-bold text-slate-800">MP3 Exports</h1>
    <button
      class="px-3 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors text-sm"
      onclick={() => (showDialog = true)}
    >
      + New Export
    </button>
  </div>

  {#if error}
    <div class="mb-4 p-3 bg-red-50 text-red-600 rounded-lg text-sm">{error}</div>
  {/if}

  {#if loading}
    <div class="flex items-center gap-2 text-slate-500">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading exports…
    </div>
  {:else if exports.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-slate-400">
      <svg class="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19V6l12-3v13M9 9l12-2M9 13l12-2" />
      </svg>
      <p class="text-lg font-medium">No exports yet</p>
      <p class="text-sm mt-1">Export a book as MP3 to listen offline</p>
      <button
        class="mt-4 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors"
        onclick={() => (showDialog = true)}
      >
        Export a Book
      </button>
    </div>
  {:else}
    <div class="space-y-2">
      {#each exports as exp (exp.id)}
        <div class="flex items-center justify-between p-3 bg-white border border-slate-200 rounded-lg">
          <div class="flex-1 min-w-0">
            <p class="font-medium text-slate-800 text-sm truncate">{exp.book_title}</p>
            <p class="text-xs text-slate-500">{exp.voice} · {exp.speed}x · {formatDate(exp.created_at)}</p>
          </div>
          <div class="flex items-center gap-3 ml-3">
            {#if exp.status === 'done'}
              <span class="text-xs text-green-600 font-medium">{formatSize(exp.file_size)}</span>
              <a
                href={`${API_BASE}/mp3/downloads/${exp.id}`}
                class="px-3 py-1 text-xs font-medium bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 transition-colors"
                download
              >
                Download
              </a>
            {:else if exp.status === 'processing' || exp.status === 'pending'}
              <div class="flex items-center gap-2">
                <svg class="w-3.5 h-3.5 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
                </svg>
                <span class="text-xs text-blue-600">{exp.progress}%</span>
              </div>
            {:else if exp.status === 'error'}
              <span class="text-xs text-red-500" title={exp.error_message ?? ''}>Error</span>
            {/if}
            <button
              class="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-red-500 transition-colors"
              onclick={() => handleDeleteExport(exp.id)}
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

{#if showDialog}
  <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onclick={() => (showDialog = false)}>
    <div class="bg-white rounded-xl p-6 max-w-md w-full shadow-xl mx-4" onclick={(e) => e.stopPropagation()}>
      <h2 class="text-lg font-bold text-slate-800 mb-4">Export as MP3</h2>

      <label class="block text-sm font-medium text-slate-700 mb-1">Book</label>
      <select bind:value={selectedBookId} class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm mb-3">
        <option value="">Select a book…</option>
        {#each books as book}
          <option value={book.id}>{book.title}</option>
        {/each}
      </select>

      <label class="block text-sm font-medium text-slate-700 mb-1">Voice</label>
      <select bind:value={selectedVoice} class="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm mb-3">
        {#each voices as voice}
          <option value={voice.id}>{voice.name} ({voice.id})</option>
        {/each}
      </select>

      <label class="block text-sm font-medium text-slate-700 mb-1">Speed</label>
      <div class="flex gap-1 mb-4">
        {#each speeds as s}
          <button
            class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors {selectedSpeed === s ? 'bg-blue-500 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}"
            onclick={() => (selectedSpeed = s)}
          >
            {s}x
          </button>
        {/each}
      </div>

      <div class="flex gap-2">
        <button
          class="flex-1 px-4 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 font-medium transition-colors text-sm"
          onclick={() => (showDialog = false)}
        >
          Cancel
        </button>
        <button
          class="flex-1 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors text-sm disabled:opacity-50"
          disabled={!selectedBookId || exporting}
          onclick={handleExport}
        >
          {exporting ? 'Exporting…' : 'Export'}
        </button>
      </div>
    </div>
  </div>
{/if}
