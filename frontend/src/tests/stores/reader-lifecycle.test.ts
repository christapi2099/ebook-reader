import { describe, it, expect, beforeEach, vi } from 'vitest'
import { get } from 'svelte/store'
import readerStore, { loadBook, seek, resetReader, flushProgress } from '$lib/stores/reader'
import type { Sentence } from '$lib/api'
import * as api from '$lib/api'

vi.mock('$lib/api')

const mk = (n: number): Sentence[] =>
  Array.from({ length: n }, (_, index) => ({
    index, text: `s${index}`, page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false, chapter: 0,
  }))

describe('reader lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetReader()
    vi.mocked(api.saveProgress).mockResolvedValue(undefined)
    vi.mocked(api.updateUserSettings).mockResolvedValue({ ok: true })
    vi.mocked(api.saveProgressBeacon).mockReturnValue(true)
  })

  it('resetReader empties the store', async () => {
    vi.mocked(api.getSentences).mockResolvedValue(mk(10))
    vi.mocked(api.getProgress).mockResolvedValue(4)
    await loadBook('a')
    resetReader()
    expect(get(readerStore)).toEqual({
      bookId: null, sentences: [], currentIndex: 0, isPlaying: false, speed: 1,
    })
  })

  it('clamps a saved index past the end of the book', async () => {
    vi.mocked(api.getSentences).mockResolvedValue(mk(10))
    vi.mocked(api.getProgress).mockResolvedValue(999)
    await loadBook('a')
    expect(get(readerStore).currentIndex).toBe(9)
  })

  it('clamps a negative saved index to 0', async () => {
    vi.mocked(api.getSentences).mockResolvedValue(mk(10))
    vi.mocked(api.getProgress).mockResolvedValue(-5)
    await loadBook('a')
    expect(get(readerStore).currentIndex).toBe(0)
  })

  it('tracks lastSavedIndex per book', async () => {
    vi.mocked(api.getSentences).mockResolvedValue(mk(10))
    vi.mocked(api.getProgress).mockResolvedValue(3)
    await loadBook('a')
    // Book b also opens at 3. Under a global lastSavedIndex, moving back to 3 in
    // book b after visiting a would be skipped; per book it must be saved.
    await loadBook('b')
    await seek(5)
    await seek(3)
    flushProgress()
    vi.mocked(api.saveProgress).mockClear()
    await loadBook('a') // a's server position is 3
    readerStore.update(s => ({ ...s, currentIndex: 3 }))
    flushProgress()
    expect(api.saveProgress).not.toHaveBeenCalled()
    await loadBook('b') // b's last known server position is 3 (seek(3))
    readerStore.update(s => ({ ...s, currentIndex: 7 }))
    flushProgress()
    expect(api.saveProgress).toHaveBeenCalledWith('b', 7)
  })

  it('does not treat another book\'s saved index as already saved', async () => {
    vi.mocked(api.getSentences).mockResolvedValue(mk(10))
    vi.mocked(api.getProgress).mockResolvedValueOnce(5)
    await loadBook('a')
    resetReader()
    readerStore.set({ bookId: 'b', sentences: mk(10), currentIndex: 5, isPlaying: false, speed: 1 })
    flushProgress()
    expect(api.saveProgress).toHaveBeenCalledWith('b', 5)
  })
})
