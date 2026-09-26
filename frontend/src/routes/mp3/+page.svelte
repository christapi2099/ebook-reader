<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { getExports, exportMP3, deleteExport, getVoices, getExportFormats, API_BASE } from '$lib/api'
  import type { Book, Voice, ExportItem, ExportFormat } from '$lib/api'
  import { getLibrary } from '$lib/api'
  import Button from '$lib/ui/Button.svelte'
  import Dialog from '$lib/ui/Dialog.svelte'
  import { toastStore } from '$lib/stores/toast'
  import { toDetailMessage } from '$lib/utils/errors'

  let exports = $state<ExportItem[]>([])
  let books = $state<Book[]>([])
  let voices = $state<Voice[]>([])
  let formats = $state<ExportFormat[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let showDialog = $state(false)
  let selectedBookId = $state('')
  let selectedVoice = $state('af_heart')
  let selectedSpeed = $state(1.0)
  let selectedFormat = $state('mp3')
  let exporting = $state(false)
  let exportError = $state<string | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

  async function fetchExports() {
    loading = true
    error = null
    try {
      const [ex, bk, vo, fmts] = await Promise.all([
        getExports(),
        getLibrary(),
        getVoices(),
        getExportFormats(),
      ])
      exports = ex
      books = bk
      voices = vo
      formats = fmts
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
    exportError = null
    try {
      await exportMP3(selectedBookId, selectedVoice, selectedSpeed, selectedFormat)
      showDialog = false
      await fetchExports()
    } catch (e: any) {
      exportError = toDetailMessage(e)
    } finally {
      exporting = false
    }
  }

  /** Re-runs the same export with the row's own settings — the only path back from a failed export short of recreating it by hand. */
  async function handleRetryExport(exp: ExportItem) {
    try {
      await exportMP3(exp.book_id, exp.voice, exp.speed, exp.format)
      await fetchExports()
    } catch (e: any) {
      toastStore.push({
        tone: 'danger',
        title: `Could not retry the export of “${exp.book_title}”`,
        message: toDetailMessage(e),
      })
    }
  }

  /**
   * Deleting only removes the database row and the finished file, so undo just
   * re-runs the same export — there is nothing else to restore.
   */
  async function handleDeleteExport(exp: ExportItem) {
    try {
      await deleteExport(exp.id)
      exports = exports.filter(e => e.id !== exp.id)
      toastStore.push({
        tone: 'info',
        title: `Deleted export of “${exp.book_title}”`,
        duration: 10000,
        action: {
          label: 'Undo',
          onClick: () => { void handleRetryExport(exp) },
        },
      })
    } catch (e: any) {
      toastStore.push({
        tone: 'danger',
        title: `Could not delete the export of “${exp.book_title}”`,
        message: toDetailMessage(e),
      })
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
    <h1 class="text-xl md:text-2xl font-bold text-fg">MP3 Exports</h1>
    <Button size="sm" onclick={() => { showDialog = true; exportError = null }}>+ New Export</Button>
  </div>

  {#if error}
    <div class="mb-4 p-3 bg-danger-soft text-danger rounded-lg text-sm">{error}</div>
  {/if}

  {#if loading}
    <div class="flex items-center gap-2 text-fg-muted">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading exports…
    </div>
  {:else if exports.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-fg-subtle">
      <svg class="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19V6l12-3v13M9 9l12-2M9 13l12-2" />
      </svg>
      <p class="text-lg font-medium">No exports yet</p>
      <p class="text-sm mt-1">Export a book as MP3 to listen offline</p>
      <Button class="mt-4" onclick={() => { showDialog = true; exportError = null }}>Export a Book</Button>
    </div>
  {:else}
    <p class="mb-3 text-xs text-fg-subtle">{exports.length} {exports.length === 1 ? 'export' : 'exports'}</p>
    <div class="space-y-2">
      {#each exports as exp (exp.id)}
        <div class="flex items-center gap-3 p-3 bg-surface border border-border rounded-lg">
          <span
            class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full {exp.status === 'done' ? 'bg-success-soft text-success' : exp.status === 'error' ? 'bg-danger-soft text-danger' : exp.status === 'pending' ? 'bg-surface-sunken text-fg-subtle' : 'bg-accent-soft text-accent'}"
            aria-hidden="true"
          >
            {#if exp.status === 'done'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.5 12.75l6 6 9-13.5" /></svg>
            {:else if exp.status === 'error'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>
            {:else if exp.status === 'pending'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6l4 2" /><circle cx="12" cy="12" r="9" stroke-width="2" /></svg>
            {:else}
              <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
              </svg>
            {/if}
          </span>

          <div class="flex-1 min-w-0">
            <p class="font-medium text-fg text-sm truncate">{exp.book_title}</p>
            <p class="text-xs text-fg-muted">
              {exp.voice} · {exp.speed}x · {exp.format.toUpperCase()} ·
              {exp.status === 'pending' ? 'Queued' : formatDate(exp.created_at)}
            </p>
            {#if exp.status === 'processing' || exp.status === 'pending'}
              <div
                class="mt-1.5 h-1 w-full max-w-[240px] overflow-hidden rounded-full bg-surface-sunken"
                role="progressbar"
                aria-label={`Export progress for ${exp.book_title}`}
                aria-valuemin="0"
                aria-valuemax="100"
                aria-valuenow={exp.progress}
              >
                <div class="h-full rounded-full bg-accent transition-[width] duration-500" style={`width: ${exp.progress}%`}></div>
              </div>
            {/if}
          </div>

          <div class="flex items-center gap-2 flex-shrink-0">
            {#if exp.status === 'done'}
              <span class="text-xs text-fg-muted">{formatSize(exp.file_size)}</span>
              <a
                href={`${API_BASE}/mp3/downloads/${exp.id}`}
                class="px-3 py-1 text-xs font-medium bg-accent-soft text-accent rounded-lg hover:bg-accent/20 transition-colors"
                download
              >
                Download
              </a>
            {:else if exp.status === 'processing' || exp.status === 'pending'}
              <span class="text-xs text-accent tabular-nums">{exp.progress}%</span>
            {:else if exp.status === 'error'}
              <span class="text-xs text-danger" title={exp.error_message ?? ''}>Error</span>
              <button
                type="button"
                class="px-3 py-1 text-xs font-medium bg-surface-sunken text-fg rounded-lg hover:bg-accent-soft hover:text-accent transition-colors"
                onclick={() => handleRetryExport(exp)}
              >
                Retry
              </button>
            {/if}
            <button
              class="p-1 rounded hover:bg-surface-sunken text-fg-subtle hover:text-danger transition-colors"
              onclick={() => handleDeleteExport(exp)}
              aria-label={`Delete export of ${exp.book_title}`}
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
  <Dialog
    titleId="mp3-export-title"
    onClose={() => (showDialog = false)}
    closeOnBackdrop
    initialFocus="#book-select"
  >
    <div class="bg-surface rounded-xl p-6 max-w-md w-full shadow-3 mx-4">
      <h2 id="mp3-export-title" class="text-lg font-bold text-fg mb-4">Export as MP3</h2>

      <label for="book-select" class="block text-sm font-medium text-fg mb-1">Book</label>
      <select id="book-select" bind:value={selectedBookId} class="w-full px-3 py-2 border border-border-strong bg-surface text-fg rounded-lg text-sm mb-3">
        <option value="">Select a book…</option>
        {#each books as book}
          <option value={book.id}>{book.title}</option>
        {/each}
      </select>

      <label for="voice-select" class="block text-sm font-medium text-fg mb-1">Voice</label>
      <select id="voice-select" bind:value={selectedVoice} class="w-full px-3 py-2 border border-border-strong bg-surface text-fg rounded-lg text-sm mb-3">
        {#each voices as voice}
          <option value={voice.id}>{voice.name} ({voice.id})</option>
        {/each}
      </select>

      {#if formats.length > 1}
        <label id="format-label" for="format-group" class="block text-sm font-medium text-fg mb-1">Format</label>
        <div id="format-group" class="flex gap-1 mb-3" role="radiogroup" aria-labelledby="format-label">
          {#each formats as fmt (fmt.id)}
            <Button
              size="sm"
              variant={selectedFormat === fmt.id ? 'primary' : 'ghost'}
              disabled={!fmt.available}
              title={!fmt.available ? (fmt.reason ?? 'Unavailable') : undefined}
              onclick={() => (selectedFormat = fmt.id)}
              role="radio"
              aria-checked={selectedFormat === fmt.id}
              aria-label={fmt.label}
            >
              {fmt.label}
            </Button>
          {/each}
        </div>
      {/if}

      <label id="speed-label" for="speed-group" class="block text-sm font-medium text-fg mb-1">Speed</label>
      <div id="speed-group" class="flex gap-1 mb-4" role="radiogroup" aria-labelledby="speed-label">
        {#each speeds as s}
          <Button
            size="sm"
            variant={selectedSpeed === s ? 'primary' : 'ghost'}
            onclick={() => (selectedSpeed = s)}
            role="radio"
            aria-checked={selectedSpeed === s}
            aria-label={`${s}x speed`}
          >
            {s}x
          </Button>
        {/each}
      </div>

      {#if exportError}
        <p class="mb-3 text-sm text-danger" role="alert">{exportError}</p>
      {/if}

      <div class="flex gap-2">
        <Button
          variant="secondary"
          class="flex-1"
          onclick={() => (showDialog = false)}
        >
          Cancel
        </Button>
        <Button
          class="flex-1"
          disabled={!selectedBookId || exporting}
          onclick={handleExport}
        >
          {exporting ? 'Exporting…' : 'Export'}
        </Button>
      </div>
    </div>
  </Dialog>
{/if}
