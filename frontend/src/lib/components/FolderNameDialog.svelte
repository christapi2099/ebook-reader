<script lang="ts">
  import { untrack } from 'svelte'
  import { overlayLayer } from '$lib/actions/overlay-layer'
  import { toDetailMessage } from '$lib/utils/errors'
  import type { Folder } from '$lib/api'

  /**
   * Create (folder = null) or rename a folder. Names are validated by the
   * backend — blank, duplicate (case-insensitive) and too long all come back as
   * 400/409 with a human-readable `detail`, which is what gets shown here
   * rather than a second, client-side set of rules.
   */
  let { folder, onClose, onSave }: {
    folder: Folder | null
    onClose: () => void
    onSave: (name: string) => Promise<void>
  } = $props()

  const isRename = $derived(folder !== null)
  // The name is seeded once, when the dialog opens; the caller remounts this
  // component (keyed by folder) rather than swapping the prop underneath it.
  let name = $state(untrack(() => folder?.name ?? ''))
  let saving = $state(false)
  let error = $state<string | null>(null)

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault()
    if (saving) return
    saving = true
    error = null
    try {
      await onSave(name)
    } catch (e) {
      error = toDetailMessage(e)
    } finally {
      saving = false
    }
  }
</script>

<div
  class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
  role="dialog"
  aria-modal="true"
  aria-labelledby="folder-name-title"
  tabindex="-1"
  use:overlayLayer={{ onClose, closeOnBackdrop: true, initialFocus: '#folder-name-input' }}
>
  <form class="mx-4 w-full max-w-sm rounded-xl bg-surface p-6 shadow-3" onsubmit={handleSubmit}>
    <h2 id="folder-name-title" class="text-lg font-bold text-fg">
      {isRename ? 'Rename folder' : 'New folder'}
    </h2>

    <label for="folder-name-input" class="mt-4 block text-sm font-medium text-fg">Folder name</label>
    <input
      id="folder-name-input"
      bind:value={name}
      disabled={saving}
      autocomplete="off"
      class="mt-1 min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-sm text-fg placeholder:text-fg-subtle disabled:opacity-60"
      placeholder="Reading list"
    />

    {#if error}
      <p class="mt-2 text-sm text-danger" role="alert">{error}</p>
    {/if}

    <div class="mt-6 flex justify-end gap-2">
      <button
        type="button"
        class="min-h-11 rounded-lg border border-border px-4 text-sm text-fg hover:bg-surface-sunken"
        onclick={onClose}
      >
        Cancel
      </button>
      <button
        type="submit"
        class="min-h-11 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50"
        disabled={saving}
      >
        {isRename ? 'Rename' : 'Create'}
      </button>
    </div>
  </form>
</div>
