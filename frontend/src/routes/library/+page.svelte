<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte'
  import { goto } from '$app/navigation'
  import {
    createFolder,
    deleteBook,
    deleteFolder,
    getFolders,
    getLibrary,
    reprocessBook,
    renameFolder,
    setBookFolder,
    updateBook,
    type Book,
    type Folder,
  } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'
  import type { StoredProgress } from '$lib/utils/reading-progress'
  import BookGrid from '$lib/components/BookGrid.svelte'
  import Button from '$lib/ui/Button.svelte'
  import BookMetadataDialog from '$lib/components/BookMetadataDialog.svelte'
  import FolderTile from '$lib/components/FolderTile.svelte'
  import FolderNameDialog from '$lib/components/FolderNameDialog.svelte'
  import MoveToFolderDialog from '$lib/components/MoveToFolderDialog.svelte'
  import LastRead from '$lib/components/LastRead.svelte'
  import { anyJobActive } from '$lib/utils/render-status'
  import { toastStore } from '$lib/stores/toast'
  import { userStore } from '$lib/stores/user'

  let books = $state<Book[]>([])
  let folders = $state<Folder[]>([])
  /** Reading positions for the cards, keyed by book id. */
  let progress = $state<Record<string, StoredProgress>>({})
  let loading = $state(true)
  let error = $state<string | null>(null)
  let foldersError = $state<string | null>(null)

  /** `null` is the "All books" view; otherwise the folder being browsed. */
  let openFolderId = $state<number | null>(null)

  /** Set while a book card is being dragged, so the drop hint can appear. */
  let draggingBookId = $state<string | null>(null)

  /** Non-null while the create/rename dialog is open. */
  let nameDialog = $state<{ folder: Folder | null } | null>(null)
  let movingBook = $state<Book | null>(null)
  /** Non-null while a book's title/author dialog is open. */
  let editingBook = $state<Book | null>(null)

  let searchQuery = $state('')
  /** `null` means "All formats". */
  let formatFilter = $state<string | null>(null)

  const currentFolder = $derived(folders.find(f => f.id === openFolderId) ?? null)
  const folderBooks = $derived(
    openFolderId === null ? books : books.filter(b => b.folder_id === openFolderId),
  )
  const availableFormats = $derived(
    Array.from(new Set(books.map(b => b.file_type))).sort(),
  )
  const visibleBooks = $derived(
    folderBooks.filter(b => {
      if (formatFilter && b.file_type !== formatFilter) return false
      const q = searchQuery.trim().toLowerCase()
      if (!q) return true
      return b.title.toLowerCase().includes(q) || (b.author ?? '').toLowerCase().includes(q)
    }),
  )
  const isFiltering = $derived(searchQuery.trim() !== '' || formatFilter !== null)

  /** How often the library refreshes while any render job is active. */
  const POLL_MS = 3000
  let pollTimer: ReturnType<typeof setInterval> | null = null
  const jobsActive = $derived(anyJobActive(books))

  /** Refresh just the books, silently: no spinner, no error banner. */
  async function pollBooks() {
    try {
      const next = await getLibrary()
      books = next
      loadProgress(next)
    } catch {
      // A missed tick is harmless; the next one retries.
    }
  }

  $effect(() => {
    if (jobsActive) {
      pollTimer ??= setInterval(pollBooks, POLL_MS)
    } else if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  })

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  })

  async function handleRetryRender(bookId: string) {
    try {
      await reprocessBook(bookId)
      await pollBooks()
    } catch (e) {
      toastStore.push({ tone: 'danger', title: 'Could not retry processing', message: toDetailMessage(e) })
    }
  }

  async function fetchLibrary() {
    loading = true
    error = null
    foldersError = null
    const [booksResult, foldersResult] = await Promise.allSettled([getLibrary(), getFolders()])

    if (booksResult.status === 'fulfilled') {
      books = booksResult.value
      // Cards appear first; their progress bars fill in when the reads land.
      void loadProgress(booksResult.value)
    } else {
      error = 'Could not connect to the backend. Make sure it is running on port 8000.'
    }

    if (foldersResult.status === 'fulfilled') {
      folders = foldersResult.value
    } else {
      foldersError = toDetailMessage(foldersResult.reason)
    }

    forgetClosedFolder()
    loading = false
  }

  /**
   * Read every visible book's position.
   *
   * Both halves arrive with the books themselves: `sentence_index` is the
   * position and `sentence_count` the total, both on `GET /library`. This used to
   * cost a `GET /library/{id}/progress` per book plus a
   * `GET /documents/{id}/sentences` per *started* book — the latter returning
   * every sentence with word bounding boxes, to read a single integer. It now
   * costs no extra requests at all, so it is synchronous.
   *
   * `sentence_index` is `null` for a book that was never started and `0` for one
   * parked on its first sentence; neither yields a bar, and neither does a book
   * with no known total. An honest gap beats a guessed percentage.
   */
  function loadProgress(forBooks: Book[]) {
    const next: Record<string, StoredProgress> = {}
    for (const book of forBooks) {
      const sentenceIndex = book.sentence_index ?? 0
      const total = book.sentence_count ?? 0
      next[book.id] = {
        sentenceIndex,
        totalSentences: sentenceIndex > 0 && total > 0 ? total : null,
      }
    }
    progress = next
  }

  /**
   * Reload both lists after a change. Failures are reported as a toast rather
   * than replacing the grid, because the change itself already succeeded.
   */
  async function refresh() {
    try {
      const [nextBooks, nextFolders] = await Promise.all([getLibrary(), getFolders()])
      books = nextBooks
      folders = nextFolders
      void loadProgress(nextBooks)
      forgetClosedFolder()
    } catch (e) {
      toastStore.push({
        tone: 'danger',
        title: 'Could not refresh the library',
        message: toDetailMessage(e),
      })
    }
  }

  /** Leaving a deleted folder open would strand the user in an empty view. */
  function forgetClosedFolder() {
    if (openFolderId === null) return
    if (!folders.some(f => f.id === openFolderId)) openFolderId = null
  }

  async function handleDelete(bookId: string) {
    if (!confirm('Delete this book and all its data?')) return
    try {
      await deleteBook(bookId)
      await refresh()
    } catch (e) {
      error = toDetailMessage(e)
    }
  }

  async function handleSetBookFolder(bookId: string, folderId: number | null) {
    await setBookFolder(bookId, folderId)
    await refresh()
    const name = folders.find(f => f.id === folderId)?.name
    toastStore.push({
      tone: 'success',
      title: name ? `Moved to “${name}”` : 'Moved to All books',
    })
  }

  async function handleDropBook(bookId: string, folder: Folder) {
    try {
      await handleSetBookFolder(bookId, folder.id)
    } catch (e) {
      toastStore.push({
        tone: 'danger',
        title: `Could not move that book to “${folder.name}”`,
        message: toDetailMessage(e),
      })
    }
  }

  /** Drag a book onto the breadcrumb to take it back out of the folder. */
  async function handleDropOnAllBooks(event: DragEvent) {
    event.preventDefault()
    const bookId = event.dataTransfer?.getData('text/plain') ?? ''
    if (!bookId) return
    try {
      await handleSetBookFolder(bookId, null)
    } catch (e) {
      toastStore.push({
        tone: 'danger',
        title: 'Could not move that book',
        message: toDetailMessage(e),
      })
    }
  }

  /**
   * Create or rename. Thrown errors (400/409 from the server) are rendered
   * inside the dialog, so it stays open with the offending name.
   */
  async function handleSaveFolderName(name: string) {
    const folder = nameDialog?.folder ?? null
    if (folder) {
      await renameFolder(folder.id, name)
    } else {
      await createFolder(name)
    }
    closeNameDialog()
    await refresh()
    toastStore.push({
      tone: 'success',
      title: folder ? `Renamed to “${name.trim()}”` : `Created “${name.trim()}”`,
    })
  }

  /**
   * Save a book's title and author. Thrown errors (400 from the server) are
   * rendered inside the dialog, so it stays open on the offending value; on
   * success the card is re-rendered from the refetched library, so it shows what
   * the server stored rather than what was typed.
   */
  async function handleSaveBookMetadata(changes: { title: string; author: string | null }) {
    const book = editingBook
    if (!book) return
    await updateBook(book.id, changes)
    closeMetadataDialog()
    await refresh()
    toastStore.push({ tone: 'success', title: 'Book details saved' })
  }

  async function handleDeleteFolder(folder: Folder) {
    const filedBookIds = books.filter(b => b.folder_id === folder.id).map(b => b.id)
    const confirmed = confirm(
      `Delete the folder “${folder.name}”? Its books stay in your library.`,
    )
    if (!confirmed) return

    try {
      const result = await deleteFolder(folder.id)
      if (openFolderId === folder.id) openFolderId = null
      await refresh()
      toastStore.push({
        tone: 'info',
        title: `Deleted “${folder.name}”`,
        message:
          result.unfiled_books === 1
            ? '1 book moved to All books'
            : `${result.unfiled_books} books moved to All books`,
        duration: 10000,
        action: {
          label: 'Undo',
          onClick: () => { void undoDeleteFolder(folder.name, filedBookIds) },
        },
      })
    } catch (e) {
      toastStore.push({
        tone: 'danger',
        title: `Could not delete “${folder.name}”`,
        message: toDetailMessage(e),
      })
    }
  }

  /**
   * Undo recreates the folder and refiles the books that were in it. The books
   * were never deleted, so nothing else has to be restored.
   */
  async function undoDeleteFolder(name: string, bookIds: string[]) {
    try {
      const restored = await createFolder(name)
      await Promise.all(bookIds.map(id => setBookFolder(id, restored.id)))
      await refresh()
      toastStore.push({ tone: 'success', title: `Restored “${name}”` })
    } catch (e) {
      toastStore.push({
        tone: 'danger',
        title: `Could not restore “${name}”`,
        message: toDetailMessage(e),
      })
    }
  }

  /**
   * Close a dialog and hand focus back to the control that opened it.
   *
   * Both dialogs open from a menu item, and that item is gone from the DOM by
   * the time the dialog closes, so `overlayLayer` has no trigger left to
   * restore to. The menu's own button is the lasting equivalent. Focus is
   * restored after the update so the dialog's teardown cannot overwrite it.
   */
  function closeDialogRestoringFocus(selector: string) {
    void tick().then(() => document.querySelector<HTMLElement>(selector)?.focus())
  }

  function closeNameDialog() {
    const folder = nameDialog?.folder ?? null
    nameDialog = null
    if (folder) closeDialogRestoringFocus(`[data-folder-options="${folder.id}"]`)
  }

  function closeMoveDialog() {
    const book = movingBook
    movingBook = null
    if (book) closeDialogRestoringFocus(`[data-book-options="${book.id}"]`)
  }

  function closeMetadataDialog() {
    const book = editingBook
    editingBook = null
    if (book) closeDialogRestoringFocus(`[data-book-options="${book.id}"]`)
  }

  function handleResumeReading() {
    const state = $userStore
    if (state.settings.last_book_id) {
      goto(`/reader/${state.settings.last_book_id}`)
    }
  }

  onMount(async () => {
    // Load user settings first so LastRead card can check last_book_id
    await userStore.load()
    await fetchLibrary()
  })
</script>

<div class="p-4 md:p-6">
  <div class="mb-6 flex items-start justify-between gap-4">
    <div>
      <h1 class="text-xl md:text-2xl font-bold text-fg">Library</h1>
      {#if currentFolder}
        <nav class="mt-2" aria-label="Breadcrumb">
          <ol class="flex items-center gap-2 text-sm">
            <li>
              <button
                type="button"
                class="min-h-11 rounded-md px-2 -mx-2 text-fg-muted hover:bg-surface-sunken hover:text-fg"
                onclick={() => (openFolderId = null)}
                ondragover={(e) => e.preventDefault()}
                ondrop={handleDropOnAllBooks}
              >
                All books
              </button>
            </li>
            <li aria-hidden="true" class="text-fg-subtle">/</li>
            <li aria-current="page" class="font-semibold text-fg">{currentFolder.name}</li>
          </ol>
        </nav>
      {/if}
    </div>

    {#if !currentFolder}
      <Button class="shrink-0" onclick={() => (nameDialog = { folder: null })}>
        New folder
      </Button>
    {/if}
  </div>

  <!-- Last read card at the top of the unfiled view -->
  {#if !currentFolder && $userStore.settings.last_book_id}
    <div class="mb-6">
      <LastRead
        bookId={$userStore.settings.last_book_id}
        sentenceIndex={$userStore.settings.last_sentence_index}
        onClick={handleResumeReading}
      />
    </div>
  {/if}

  {#if !currentFolder && foldersError && !error}
    <div class="mb-6 flex flex-col items-start gap-2 rounded-lg border border-danger bg-danger-soft p-4">
      <p class="text-sm font-medium text-danger">{foldersError}</p>
      <Button variant="secondary" onclick={fetchLibrary}>Retry</Button>
    </div>
  {/if}

  {#if !currentFolder && folders.length > 0}
    <section class="mb-6" aria-labelledby="folders-heading">
      <h2 id="folders-heading" class="mb-3 text-sm font-semibold uppercase tracking-wide text-fg-muted">
        Folders
      </h2>
      {#if draggingBookId}
        <p class="mb-2 text-sm text-fg-muted" data-drop-hint="true">
          Drop the book onto a folder to file it.
        </p>
      {/if}
      <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {#each folders as folder (folder.id)}
          <FolderTile
            {folder}
            onOpen={(f) => (openFolderId = f.id)}
            onRename={(f) => (nameDialog = { folder: f })}
            onDelete={handleDeleteFolder}
            onDropBook={handleDropBook}
          />
        {/each}
      </div>
    </section>
  {/if}

  {#if currentFolder}
    <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-fg-muted">
      {currentFolder.name}
    </h2>
  {/if}

  {#if !loading && !error && (books.length > 0 || isFiltering)}
    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <label class="relative w-full sm:max-w-xs">
        <span class="sr-only">Search books</span>
        <svg
          class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle"
          fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z" />
        </svg>
        <input
          type="search"
          placeholder="Search title or author"
          bind:value={searchQuery}
          class="w-full min-h-11 rounded-md border border-border bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
        />
      </label>

      {#if availableFormats.length > 1}
        <div class="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by format">
          <button
            type="button"
            class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
            class:border-accent={formatFilter === null}
            class:bg-accent-soft={formatFilter === null}
            class:text-accent={formatFilter === null}
            class:border-border={formatFilter !== null}
            class:text-fg-muted={formatFilter !== null}
            aria-pressed={formatFilter === null}
            onclick={() => (formatFilter = null)}
          >
            All
          </button>
          {#each availableFormats as format (format)}
            <button
              type="button"
              class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium uppercase transition-colors"
              class:border-accent={formatFilter === format}
              class:bg-accent-soft={formatFilter === format}
              class:text-accent={formatFilter === format}
              class:border-border={formatFilter !== format}
              class:text-fg-muted={formatFilter !== format}
              aria-pressed={formatFilter === format}
              onclick={() => (formatFilter = format)}
            >
              {format}
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  {#if !loading && !error && books.length > 0 && isFiltering && visibleBooks.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-center">
      <svg class="mb-4 h-16 w-16 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z" />
      </svg>
      <p class="text-lg font-medium text-fg-muted">No books match your search</p>
      <p class="mt-1 text-sm text-fg-subtle">Try a different title, author, or format.</p>
      <Button variant="secondary" class="mt-4" onclick={() => { searchQuery = ''; formatFilter = null }}>
        Clear filters
      </Button>
    </div>
  {:else if currentFolder && !loading && !error && !isFiltering && visibleBooks.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-center">
      <svg class="mb-4 h-16 w-16 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="1.5"
          d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
        />
      </svg>
      <p class="text-lg font-medium text-fg-muted">This folder is empty</p>
      <p class="mt-1 text-sm text-fg-subtle">
        Drag a book onto a folder tile, or use “Move to folder” on any book.
      </p>
      <Button class="mt-4" onclick={() => (openFolderId = null)}>Back to all books</Button>
    </div>
  {:else}
    <BookGrid
      books={visibleBooks}
      {loading}
      {error}
      {progress}
      onRetry={fetchLibrary}
      onRetryRender={handleRetryRender}
      onClick={(id) => goto(`/reader/${id}`)}
      onDelete={handleDelete}
      onMove={(book) => (movingBook = book)}
      onEditMetadata={(book) => (editingBook = book)}
      onDragStart={(book) => (draggingBookId = book.id)}
      onDragEnd={() => (draggingBookId = null)}
    />
  {/if}
</div>

{#if nameDialog}
  {#key nameDialog.folder?.id ?? 'new'}
    <FolderNameDialog
      folder={nameDialog.folder}
      onClose={closeNameDialog}
      onSave={handleSaveFolderName}
    />
  {/key}
{/if}

{#if movingBook}
  {@const book = movingBook}
  {#key book.id}
    <MoveToFolderDialog
      book={book}
      {folders}
      onClose={closeMoveDialog}
      onMove={(folderId) => handleSetBookFolder(book.id, folderId)}
    />
  {/key}
{/if}

{#if editingBook}
  {@const book = editingBook}
  {#key book.id}
    <BookMetadataDialog
      book={book}
      onClose={closeMetadataDialog}
      onSave={handleSaveBookMetadata}
    />
  {/key}
{/if}
