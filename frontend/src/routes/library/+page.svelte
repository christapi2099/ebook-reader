<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { get } from 'svelte/store'
  import { goto } from '$app/navigation'
  import {
    createFolder,
    deleteBook,
    deleteFolder,
    getFolders,
    getLibrary,
    getProgress,
    getSentences,
    renameFolder,
    setBookFolder,
    type Book,
    type Folder,
  } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'
  import type { StoredProgress } from '$lib/utils/reading-progress'
  import readerStore from '$lib/stores/reader'
  import BookGrid from '$lib/components/BookGrid.svelte'
  import FolderTile from '$lib/components/FolderTile.svelte'
  import FolderNameDialog from '$lib/components/FolderNameDialog.svelte'
  import MoveToFolderDialog from '$lib/components/MoveToFolderDialog.svelte'
  import LastRead from '$lib/components/LastRead.svelte'
  import { toastStore } from '$lib/stores/toast'
  import { userStore } from '$lib/stores/user'

  let books = $state<Book[]>([])
  let folders = $state<Folder[]>([])
  /** Reading positions for the cards, keyed by book id. */
  let progress = $state<Record<string, StoredProgress>>({})
  let loading = $state(true)
  let error = $state<string | null>(null)
  let foldersError = $state<string | null>(null)

  /** Discriminates progress loads, so a slow one cannot overwrite a newer one. */
  let progressRun = 0

  /** `null` is the "All books" view; otherwise the folder being browsed. */
  let openFolderId = $state<number | null>(null)

  /** Set while a book card is being dragged, so the drop hint can appear. */
  let draggingBookId = $state<string | null>(null)

  /** Non-null while the create/rename dialog is open. */
  let nameDialog = $state<{ folder: Folder | null } | null>(null)
  let movingBook = $state<Book | null>(null)

  const currentFolder = $derived(folders.find(f => f.id === openFolderId) ?? null)
  const visibleBooks = $derived(
    openFolderId === null ? books : books.filter(b => b.folder_id === openFolderId),
  )

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
   * `GET /library/{book_id}/progress` is the only place a position lives, and it
   * returns the sentence index without a total, so the total comes from
   * `GET /documents/{book_id}/sentences` — asked for only on books that have
   * actually been started, and skipped when the reader store already holds that
   * book's sentences from this session. A book whose total cannot be read gets
   * no bar: an honest gap beats a guessed percentage.
   */
  async function loadProgress(forBooks: Book[]) {
    const run = ++progressRun
    const loaded = get(readerStore)

    const results = await Promise.allSettled(
      forBooks.map(async (book): Promise<[string, StoredProgress]> => {
        const sentenceIndex = await getProgress(book.id)
        if (sentenceIndex <= 0) return [book.id, { sentenceIndex, totalSentences: null }]

        const totalSentences =
          loaded.bookId === book.id && loaded.sentences.length > 0
            ? loaded.sentences.length
            : (await getSentences(book.id)).length

        return [book.id, { sentenceIndex, totalSentences }]
      }),
    )

    if (run !== progressRun) return

    const next: Record<string, StoredProgress> = {}
    for (const result of results) {
      if (result.status !== 'fulfilled') continue
      const [bookId, entry] = result.value
      next[bookId] = entry
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
      <h1 class="text-xl md:text-2xl font-bold text-slate-800">Library</h1>
      {#if currentFolder}
        <nav class="mt-2" aria-label="Breadcrumb">
          <ol class="flex items-center gap-2 text-sm">
            <li>
              <button
                type="button"
                class="min-h-11 rounded-md px-2 -mx-2 text-slate-600 hover:bg-slate-100 hover:text-slate-800"
                onclick={() => (openFolderId = null)}
                ondragover={(e) => e.preventDefault()}
                ondrop={handleDropOnAllBooks}
              >
                All books
              </button>
            </li>
            <li aria-hidden="true" class="text-slate-400">/</li>
            <li aria-current="page" class="font-semibold text-slate-800">{currentFolder.name}</li>
          </ol>
        </nav>
      {/if}
    </div>

    {#if !currentFolder}
      <button
        type="button"
        class="min-h-11 shrink-0 rounded-lg bg-blue-500 px-4 text-sm font-medium text-white hover:bg-blue-600"
        onclick={() => (nameDialog = { folder: null })}
      >
        New folder
      </button>
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
    <div class="mb-6 flex flex-col items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4">
      <p class="text-sm font-medium text-red-600">{foldersError}</p>
      <button
        class="min-h-11 rounded-lg bg-white px-4 text-sm text-slate-700 hover:bg-slate-100"
        onclick={fetchLibrary}
      >
        Retry
      </button>
    </div>
  {/if}

  {#if !currentFolder && folders.length > 0}
    <section class="mb-6" aria-labelledby="folders-heading">
      <h2 id="folders-heading" class="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Folders
      </h2>
      {#if draggingBookId}
        <p class="mb-2 text-sm text-slate-500" data-drop-hint="true">
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
    <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
      {currentFolder.name}
    </h2>
  {/if}

  {#if currentFolder && !loading && !error && visibleBooks.length === 0}
    <div class="flex flex-col items-center justify-center py-20 text-center">
      <svg class="mb-4 h-16 w-16 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="1.5"
          d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
        />
      </svg>
      <p class="text-lg font-medium text-slate-500">This folder is empty</p>
      <p class="mt-1 text-sm text-slate-400">
        Drag a book onto a folder tile, or use “Move to folder” on any book.
      </p>
      <button
        type="button"
        class="mt-4 min-h-11 rounded-lg bg-blue-500 px-4 text-sm font-medium text-white hover:bg-blue-600"
        onclick={() => (openFolderId = null)}
      >
        Back to all books
      </button>
    </div>
  {:else}
    <BookGrid
      books={visibleBooks}
      {loading}
      {error}
      {progress}
      onRetry={fetchLibrary}
      onClick={(id) => goto(`/reader/${id}`)}
      onDelete={handleDelete}
      onMove={(book) => (movingBook = book)}
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
