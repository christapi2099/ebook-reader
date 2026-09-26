<script lang="ts">
  import type { Folder } from '$lib/api'

  /**
   * One folder tile: the whole tile opens the folder and doubles as the
   * drag-and-drop target for filing a book. Dragging is an enhancement only —
   * every action it offers is also reachable from the tile menu and from the
   * per-book "Move to folder" dialog.
   */
  let {
    folder,
    onOpen,
    onRename,
    onDelete,
    onDropBook,
  }: {
    folder: Folder
    onOpen: (folder: Folder) => void
    onRename: (folder: Folder) => void
    onDelete: (folder: Folder) => void
    onDropBook: (bookId: string, folder: Folder) => void
  } = $props()

  let menuOpen = $state(false)
  let dropActive = $state(false)
  let menu = $state<HTMLDivElement | null>(null)
  let optionsButton = $state<HTMLButtonElement | null>(null)

  const countLabel = $derived(
    folder.book_count === 1 ? '1 book' : `${folder.book_count} books`,
  )

  /**
   * Close the menu on an outside pointer press. `pointerdown` rather than
   * `click`: it has already finished propagating by the time this listener
   * exists, so the press that opened the menu cannot immediately close it.
   */
  $effect(() => {
    if (!menuOpen) return
    const root = menu

    function handlePointerDown(event: PointerEvent) {
      if (root && event.target instanceof Node && root.contains(event.target)) return
      menuOpen = false
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  })

  function closeMenu() {
    menuOpen = false
    optionsButton?.focus()
  }

  function handleMenuKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    closeMenu()
  }

  function handleDragOver(event: DragEvent) {
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
    dropActive = true
  }

  function handleDrop(event: DragEvent) {
    event.preventDefault()
    dropActive = false
    const bookId = event.dataTransfer?.getData('text/plain') ?? ''
    if (bookId) onDropBook(bookId, folder)
  }
</script>

<div class="relative">
  <button
    type="button"
    class="flex min-h-11 w-full items-center gap-3 rounded-lg border p-4 text-left shadow-1 transition-colors {dropActive
      ? 'border-accent bg-accent-soft'
      : 'border-border bg-surface hover:border-border-strong'}"
    aria-label={`Open folder ${folder.name}, ${countLabel}`}
    onclick={() => onOpen(folder)}
    ondragover={handleDragOver}
    ondragleave={() => (dropActive = false)}
    ondrop={handleDrop}
  >
    <span class="pointer-events-none flex min-w-0 flex-1 items-center gap-3">
      <svg
        class="h-6 w-6 shrink-0 {dropActive ? 'text-accent' : 'text-fg-subtle'}"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="1.5"
          d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
        />
      </svg>
      <span class="min-w-0">
        <span class="block truncate text-sm font-semibold text-fg">{folder.name}</span>
        <span class="block text-xs text-fg-muted">{countLabel}</span>
      </span>
    </span>
  </button>

  <div class="absolute right-2 top-2" bind:this={menu}>
    <button
      type="button"
      class="flex h-11 w-11 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-surface-sunken hover:text-fg"
      aria-label={`Folder options for ${folder.name}`}
      aria-haspopup="menu"
      aria-expanded={menuOpen}
      data-folder-options={folder.id}
      bind:this={optionsButton}
      onclick={() => (menuOpen = !menuOpen)}
      onkeydown={handleMenuKeydown}
    >
      <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="5" r="1.6" />
        <circle cx="12" cy="12" r="1.6" />
        <circle cx="12" cy="19" r="1.6" />
      </svg>
    </button>

    {#if menuOpen}
      <div class="absolute right-0 top-12 z-20 min-w-[11rem] rounded-lg border border-border bg-surface-raised py-1 shadow-2" role="menu" aria-label={`Actions for folder ${folder.name}`}>
        <button
          type="button"
          role="menuitem"
          class="flex min-h-11 w-full items-center px-3 text-left text-sm text-fg hover:bg-surface-sunken"
          onclick={() => { menuOpen = false; onRename(folder) }}
          onkeydown={handleMenuKeydown}
        >
          Rename
        </button>
        <button
          type="button"
          role="menuitem"
          class="flex min-h-11 w-full items-center px-3 text-left text-sm text-danger hover:bg-danger-soft"
          onclick={() => { menuOpen = false; onDelete(folder) }}
          onkeydown={handleMenuKeydown}
        >
          Delete folder
        </button>
      </div>
    {/if}
  </div>
</div>
