<script lang="ts">
  import { untrack } from 'svelte'
  import { overlayLayer } from '$lib/actions/overlay-layer'
  import { toDetailMessage } from '$lib/utils/errors'
  import type { Book, Folder } from '$lib/api'

  /**
   * The accessible route for filing a book: a real radio group plus one primary
   * action. Dragging a book onto a folder tile does the same thing, but nothing
   * here depends on a pointer.
   */
  let { book, folders, onClose, onMove }: {
    book: Book
    folders: Folder[]
    onClose: () => void
    onMove: (folderId: number | null) => Promise<void>
  } = $props()

  // Seeded once, when the dialog opens for this book; the caller keys the
  // component by book id, so the prop never changes underneath it.
  const currentFolderId = untrack(() => book.folder_id ?? null)
  let selected = $state<number | null>(currentFolderId)
  let saving = $state(false)
  let error = $state<string | null>(null)

  function countLabel(count: number) {
    return count === 1 ? '1 book' : `${count} books`
  }

  async function handleSubmit() {
    if (saving) return
    if (selected === currentFolderId) {
      onClose()
      return
    }
    saving = true
    error = null
    try {
      await onMove(selected)
      onClose()
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
  aria-labelledby="move-dialog-title"
  tabindex="-1"
  use:overlayLayer={{ onClose, closeOnBackdrop: true, initialFocus: 'input[name="folder"]:checked' }}
>
  <div class="mx-4 w-full max-w-md rounded-xl bg-surface p-6 shadow-3">
    <h2 id="move-dialog-title" class="text-lg font-bold text-fg">Move “{book.title}”</h2>
    <p class="mt-1 text-sm text-fg-muted">Choose the folder this book belongs to.</p>

    <fieldset class="mt-4">
      <legend class="sr-only">Folder</legend>
      <div class="max-h-64 space-y-1 overflow-auto">
        <label class="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-border px-3 hover:bg-accent-soft">
          <input
            type="radio"
            name="folder"
            class="accent-accent"
            checked={selected === null}
            disabled={saving}
            onchange={() => (selected = null)}
          />
          <span class="text-sm text-fg">No folder (All books)</span>
        </label>

        {#each folders as folder (folder.id)}
          <label class="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-border px-3 hover:bg-accent-soft">
            <input
              type="radio"
              name="folder"
              class="accent-accent"
              checked={selected === folder.id}
              disabled={saving}
              onchange={() => (selected = folder.id)}
            />
            <span class="min-w-0 flex-1 truncate text-sm text-fg">{folder.name}</span>
            <span class="shrink-0 text-xs text-fg-muted">{countLabel(folder.book_count)}</span>
          </label>
        {/each}
      </div>
    </fieldset>

    {#if folders.length === 0}
      <p class="mt-3 text-sm text-fg-muted">You have no folders yet — create one from the Library first.</p>
    {/if}

    {#if error}
      <p class="mt-3 text-sm text-danger" role="alert">{error}</p>
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
        type="button"
        class="min-h-11 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50"
        disabled={saving}
        onclick={handleSubmit}
      >
        Move
      </button>
    </div>
  </div>
</div>
