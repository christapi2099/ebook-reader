import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, screen, waitFor, within, cleanup } from '@testing-library/svelte'
import { get } from 'svelte/store'
import LibraryPage from '../../routes/library/+page.svelte'
import Toaster from '$lib/components/Toaster.svelte'
import { toastStore } from '$lib/stores/toast'
import { ApiError } from '$lib/utils/errors'
import type { Book, Folder } from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getLibrary: vi.fn(),
    getFolders: vi.fn(),
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    setBookFolder: vi.fn(),
    deleteBook: vi.fn(),
    getUserSettings: vi.fn(),
  }
})

import * as api from '$lib/api'
import { goto } from '$app/navigation'

const BOOKS: Book[] = [
  {
    id: 'book-1',
    title: 'Deep Work',
    author: 'Cal Newport',
    file_type: 'PDF',
    page_count: 10,
    folder_id: null,
  },
  {
    id: 'book-2',
    title: 'The Shallows',
    author: null,
    file_type: 'EPUB',
    page_count: 20,
    folder_id: 7,
  },
]

const FOLDERS: Folder[] = [
  { id: 7, name: 'Reading list', created_at: '2026-01-01T00:00:00Z', book_count: 1 },
  { id: 8, name: 'Later', created_at: '2026-01-02T00:00:00Z', book_count: 0 },
]

/** Render the page and wait for the initial load to settle. */
async function renderLibrary() {
  // The layout mounts the Toaster next to the route; folder delete offers its
  // undo from there, so the tests render it too.
  render(Toaster)
  render(LibraryPage)
  await waitFor(() => expect(screen.getByText('Deep Work')).toBeTruthy())
}

function openBookMenu(title: string) {
  return fireEvent.click(screen.getByLabelText(`Book options for ${title}`))
}

/**
 * The drop payload a real drag would carry. jsdom has no DataTransfer, so this
 * is the minimal object the handlers read.
 */
function dataTransfer() {
  const store = new Map<string, string>()
  return {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? '',
    effectAllowed: '',
    dropEffect: '',
  }
}

async function dispatchDrag(type: string, target: Element, transfer: unknown) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'dataTransfer', { value: transfer })
  await fireEvent(target, event)
}

describe('Library folders UI', () => {
  beforeEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.mocked(api.getLibrary).mockResolvedValue(BOOKS)
    vi.mocked(api.getFolders).mockResolvedValue(FOLDERS)
    vi.mocked(api.getUserSettings).mockResolvedValue({ last_book_id: null, last_sentence_index: 0 })
    vi.mocked(api.setBookFolder).mockResolvedValue({ ok: true, folder_id: 7 })
    vi.mocked(api.createFolder).mockResolvedValue({
      id: 9,
      name: 'Restored',
      created_at: '2026-01-03T00:00:00Z',
      book_count: 0,
    })
    for (const toast of get(toastStore)) toastStore.dismiss(toast.id)
  })

  it('lists folders with the book counts the server reported', async () => {
    await renderLibrary()

    expect(screen.getByLabelText('Open folder Reading list, 1 book')).toBeTruthy()
    expect(screen.getByLabelText('Open folder Later, 0 books')).toBeTruthy()
  })

  it('opens a folder: breadcrumb plus only that folder’s books', async () => {
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Open folder Reading list, 1 book'))

    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(breadcrumb).getByRole('button', { name: 'All books' })).toBeTruthy()
    expect(within(breadcrumb).getByText('Reading list')).toBeTruthy()
    expect(screen.getByText('The Shallows')).toBeTruthy()
    expect(screen.queryByText('Deep Work')).toBeNull()
  })

  it('breadcrumb goes back to every book', async () => {
    await renderLibrary()
    await fireEvent.click(screen.getByLabelText('Open folder Reading list, 1 book'))

    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    await fireEvent.click(within(breadcrumb).getByRole('button', { name: 'All books' }))

    expect(screen.getByText('Deep Work')).toBeTruthy()
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull()
  })

  it('shows a real empty state for a folder with no books', async () => {
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Open folder Later, 0 books'))

    expect(screen.getByText('This folder is empty')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Back to all books' })).toBeTruthy()
    // The library-level "no books at all" copy must not stand in for it.
    expect(screen.queryByText('No books yet')).toBeNull()
  })

  it('clicking a book still opens the reader', async () => {
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Deep Work by Cal Newport'))

    expect(vi.mocked(goto)).toHaveBeenCalledWith('/reader/book-1')
  })

  it('move dialog files a book into a folder', async () => {
    await renderLibrary()

    await openBookMenu('Deep Work')
    await fireEvent.click(screen.getByRole('button', { name: 'Move to folder' }))
    await fireEvent.click(screen.getByRole('radio', { name: /Reading list/ }))
    await fireEvent.click(screen.getByRole('button', { name: 'Move' }))

    await waitFor(() => expect(api.setBookFolder).toHaveBeenCalledWith('book-1', 7))
  })

  it('move dialog takes a book back out of its folder', async () => {
    await renderLibrary()

    await openBookMenu('The Shallows')
    await fireEvent.click(screen.getByRole('button', { name: 'Move to folder' }))
    await fireEvent.click(screen.getByRole('radio', { name: /No folder/ }))
    await fireEvent.click(screen.getByRole('button', { name: 'Move' }))

    await waitFor(() => expect(api.setBookFolder).toHaveBeenCalledWith('book-2', null))
  })

  it('move dialog leaves the API alone when the folder is unchanged', async () => {
    await renderLibrary()

    await openBookMenu('The Shallows')
    await fireEvent.click(screen.getByRole('button', { name: 'Move to folder' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Move' }))

    expect(api.setBookFolder).not.toHaveBeenCalled()
  })

  it('surfaces the server detail when a new folder name is blank', async () => {
    vi.mocked(api.createFolder).mockRejectedValue(
      new ApiError(400, 'Bad Request', 'Folder name is required'),
    )
    await renderLibrary()

    await fireEvent.click(screen.getByRole('button', { name: 'New folder' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('Folder name is required')
  })

  it('surfaces the server detail for a duplicate name', async () => {
    vi.mocked(api.createFolder).mockRejectedValue(
      new ApiError(409, 'Conflict', 'A folder with that name already exists'),
    )
    await renderLibrary()

    await fireEvent.click(screen.getByRole('button', { name: 'New folder' }))
    await fireEvent.input(screen.getByLabelText('Folder name'), {
      target: { value: 'reading LIST' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('A folder with that name already exists')
  })

  it('surfaces the server detail when a rename collides', async () => {
    vi.mocked(api.renameFolder).mockRejectedValue(
      new ApiError(409, 'Conflict', 'A folder with that name already exists'),
    )
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Folder options for Later'))
    await fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
    await fireEvent.input(screen.getByLabelText('Folder name'), { target: { value: 'Reading list' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Rename' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('A folder with that name already exists')
    expect(api.renameFolder).toHaveBeenCalledWith(8, 'Reading list')
  })

  it('renames a folder and reloads the lists', async () => {
    vi.mocked(api.renameFolder).mockResolvedValue({ ...FOLDERS[1], name: 'Someday' })
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Folder options for Later'))
    await fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
    await fireEvent.input(screen.getByLabelText('Folder name'), { target: { value: 'Someday' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Rename' }))

    await waitFor(() => expect(api.renameFolder).toHaveBeenCalledWith(8, 'Someday'))
    await waitFor(() => expect(api.getFolders).toHaveBeenCalledTimes(2))
  })

  it('deleting a folder offers an undo that recreates it with its books', async () => {
    vi.mocked(api.deleteFolder).mockResolvedValue({ ok: true, unfiled_books: 1 })
    vi.stubGlobal('confirm', vi.fn(() => true))
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Folder options for Reading list'))
    await fireEvent.click(screen.getByRole('menuitem', { name: 'Delete folder' }))

    await waitFor(() => expect(api.deleteFolder).toHaveBeenCalledWith(7))
    const undo = await screen.findByRole('button', { name: 'Undo' })

    await fireEvent.click(undo)

    await waitFor(() => expect(api.createFolder).toHaveBeenCalledWith('Reading list'))
    await waitFor(() => expect(api.setBookFolder).toHaveBeenCalledWith('book-2', 9))
    vi.unstubAllGlobals()
  })

  it('deleting a folder never calls deleteBook', async () => {
    vi.mocked(api.deleteFolder).mockResolvedValue({ ok: true, unfiled_books: 1 })
    vi.stubGlobal('confirm', vi.fn(() => true))
    await renderLibrary()

    await fireEvent.click(screen.getByLabelText('Folder options for Reading list'))
    await fireEvent.click(screen.getByRole('menuitem', { name: 'Delete folder' }))

    await waitFor(() => expect(api.deleteFolder).toHaveBeenCalled())
    expect(api.deleteBook).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('dragging a book onto a folder tile files it', async () => {
    await renderLibrary()
    const transfer = dataTransfer()

    await dispatchDrag('dragstart', screen.getByLabelText('Deep Work by Cal Newport'), transfer)
    // The drop affordance appears while a drag is in flight.
    expect(screen.getByText('Drop the book onto a folder to file it.')).toBeTruthy()

    const tile = screen.getByLabelText('Open folder Reading list, 1 book')
    await dispatchDrag('dragover', tile, transfer)
    await dispatchDrag('drop', tile, transfer)

    await waitFor(() => expect(api.setBookFolder).toHaveBeenCalledWith('book-1', 7))
  })

  it('dragging a book onto the breadcrumb unfiles it', async () => {
    await renderLibrary()
    await fireEvent.click(screen.getByLabelText('Open folder Reading list, 1 book'))
    const transfer = dataTransfer()

    await dispatchDrag('dragstart', screen.getByLabelText('The Shallows by Unknown'), transfer)
    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    const allBooks = within(breadcrumb).getByRole('button', { name: 'All books' })
    await dispatchDrag('dragover', allBooks, transfer)
    await dispatchDrag('drop', allBooks, transfer)

    await waitFor(() => expect(api.setBookFolder).toHaveBeenCalledWith('book-2', null))
  })

  it('keeps the grid rendered when only the folder list fails', async () => {
    vi.mocked(api.getFolders).mockRejectedValue(new TypeError('Failed to fetch'))
    await renderLibrary()

    expect(screen.getByText('Deep Work')).toBeTruthy()
    expect(screen.getByText('Could not connect to the backend')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('keyboard use of the card controls does not open the book', async () => {
    await renderLibrary()
    vi.mocked(goto).mockClear()

    const options = screen.getByLabelText('Book options for Deep Work')
    options.focus()
    // jsdom does not synthesise the click a browser fires for Enter on a
    // button, so this only proves the card's own Enter handler stands down.
    await fireEvent.keyDown(options, { key: 'Enter' })
    expect(vi.mocked(goto)).not.toHaveBeenCalled()

    await fireEvent.click(options)
    expect(screen.getByRole('button', { name: 'Move to folder' })).toBeTruthy()
    expect(vi.mocked(goto)).not.toHaveBeenCalled()
  })

  it('Escape closes the move dialog and returns focus to the card’s options button', async () => {
    await renderLibrary()

    const options = screen.getByLabelText('Book options for Deep Work')
    await fireEvent.click(options)
    const moveItem = screen.getByRole('button', { name: 'Move to folder' })
    // Chromium focuses the clicked menu item; do the same before it unmounts.
    moveItem.focus()
    await fireEvent.click(moveItem)
    expect(screen.getByRole('dialog')).toBeTruthy()

    await fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(options))
  })

  it('Escape closes the new-folder dialog without creating anything', async () => {
    await renderLibrary()

    const trigger = screen.getByRole('button', { name: 'New folder' })
    trigger.focus()
    await fireEvent.click(trigger)
    await fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(api.createFolder).not.toHaveBeenCalled()
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})
