import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte'
import { readable } from 'svelte/store'
import type { Book } from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))
vi.mock('$app/stores', () => ({ page: readable({ params: { id: 'b1' } }) }))
vi.mock('$lib/components/TextViewer.svelte', async () => ({ default: (await import('./stubs/Stub.svelte')).default }))
vi.mock('$lib/components/PDFViewer.svelte', async () => ({ default: (await import('./stubs/Stub.svelte')).default }))
vi.mock('$lib/stores/audio', async () => {
  const { writable } = await import('svelte/store')
  let state: Record<string, unknown> = {
    isPlaying: false, currentIndex: 0, currentWordIndex: -1, buffering: false,
    elapsedSeconds: 0, sentenceDurations: {},
  }
  const subs = new Set<(v: unknown) => void>()
  return {
    audioStore: {
      subscribe: (fn: (v: unknown) => void) => { subs.add(fn); fn(state); return () => { subs.delete(fn) } },
      init: vi.fn(), load: vi.fn(), destroy: vi.fn(), play: vi.fn(), pause: vi.fn(), seek: vi.fn(),
      setSpeed: vi.fn(), setCurrentIndex: vi.fn(), setVoice: vi.fn(),
      __set: (patch: object) => { state = { ...state, ...patch }; subs.forEach(f => f(state)) },
    },
    speedDowngradeStore: writable(null),
  }
})
vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getBook: vi.fn(), getSentences: vi.fn(), getRenderManifest: vi.fn(), getProgress: vi.fn(),
    getVoices: vi.fn(), setBookVoice: vi.fn(), reprocessBook: vi.fn(),
    saveProgress: vi.fn(), saveProgressBeacon: vi.fn(), getUserSettings: vi.fn(),
    updateUserSettings: vi.fn(), createBookmark: vi.fn(),
  }
})

import * as api from '$lib/api'
import { audioStore } from '$lib/stores/audio'
import readerStore from '$lib/stores/reader'
import { get } from 'svelte/store'
import ReaderPage from '../../routes/reader/[id]/+page.svelte'

const sent = (n: number) => Array.from({ length: n }, (_, i) => ({
  index: i, text: `s${i}`, page: 0, x0: 0, y0: 0, x1: 0, y1: 0, filtered: false, chapter: 0,
}))
const book = (extra: Partial<Book>): Book => ({
  id: 'b1', title: 'T', author: null, file_type: 'text', page_count: 3, folder_id: null, ...extra,
})
const manifest = { voice: 'af_heart', tiers: {} } as never

async function flush(ms = 0) { await vi.advanceTimersByTimeAsync(ms) }
/** Let the open/load promise chain finish without moving the poll timers. */
async function settle() { for (let i = 0; i < 100; i++) await flush(0) }

describe('reader render gate', () => {
  beforeEach(() => {
    cleanup(); vi.useFakeTimers()
    ;(audioStore as unknown as { __set: (p: object) => void }).__set({ isPlaying: false })
    vi.resetAllMocks()
    vi.mocked(audioStore.play).mockImplementation(() => { (audioStore as unknown as { __set: (p: object) => void }).__set({ isPlaying: true }) })
    vi.mocked(api.getSentences).mockResolvedValue(sent(5) as never)
    vi.mocked(api.getProgress).mockResolvedValue(2)
    vi.mocked(api.getRenderManifest).mockResolvedValue(manifest)
    vi.mocked(api.getVoices).mockResolvedValue([])
    vi.mocked(api.getUserSettings).mockResolvedValue({} as never)
    vi.mocked(api.saveProgress).mockResolvedValue(undefined as never)
    vi.mocked(api.updateUserSettings).mockResolvedValue({} as never)
  })
  afterEach(() => { vi.useRealTimers() })

  it('shows the processing screen with no ready generation, then loads once ready', async () => {
    vi.mocked(api.getBook)
      .mockResolvedValueOnce(book({ ready_generation: null, job_status: 'processing', progress: 0.3, eta_seconds: 120 }))
      .mockResolvedValueOnce(book({ ready_generation: null, job_status: 'processing', progress: 0.3 }))
      .mockResolvedValue(book({ ready_generation: 1, job_status: 'ready' }))
    render(ReaderPage)
    await flush()
    expect(document.querySelector('[data-processing-screen]')).toBeTruthy()
    expect(api.getSentences).not.toHaveBeenCalled()

    await flush(3000)
    await flush(3000)
    expect(document.querySelector('[data-processing-screen]')).toBeNull()
    expect(api.getSentences).toHaveBeenCalledWith('b1', 1)
    expect(api.getRenderManifest).toHaveBeenCalledWith('b1')
    expect(audioStore.init).toHaveBeenCalledWith('b1')
  })

  it('goes back to the processing screen when sentences answer 409', async () => {
    vi.mocked(api.getBook)
      .mockResolvedValueOnce(book({ ready_generation: 1 }))
      .mockResolvedValue(book({ ready_generation: null, job_status: 'processing' }))
    vi.mocked(api.getSentences).mockRejectedValueOnce(new api.NotReadyError('b1'))
    render(ReaderPage)
    await flush()
    expect(document.querySelector('[data-processing-screen]')).toBeTruthy()
  })

  it('while playing, a newer generation shows the banner and switching reloads', async () => {
    vi.mocked(api.getBook).mockResolvedValue(book({ ready_generation: 1 }))
    render(ReaderPage)
    await settle()
    vi.mocked(api.getBook).mockResolvedValue(book({ ready_generation: 2, voice: 'af_bella' }))
    await fireEvent.click(screen.getByRole('button', { name: /^play/i }))
    await settle()
    expect(get(readerStore).isPlaying).toBe(true)
    await flush(10_000)

    expect(screen.getByText('New version ready (voice af_bella)')).toBeTruthy()
    expect(api.getSentences).toHaveBeenCalledTimes(1)

    vi.mocked(api.getProgress).mockResolvedValue(4) // backend-remapped position
    await fireEvent.click(screen.getByRole('button', { name: 'Switch' }))
    await flush()
    expect(audioStore.destroy).toHaveBeenCalled()
    expect(api.getSentences).toHaveBeenLastCalledWith('b1', 2)
    expect(get(readerStore).currentIndex).toBe(4)
    expect(screen.queryByText(/New version ready/)).toBeNull()
  })

  it('while paused, a newer generation is switched to automatically', async () => {
    vi.mocked(api.getBook).mockResolvedValue(book({ ready_generation: 1 }))
    render(ReaderPage)
    await settle()
    vi.mocked(api.getBook).mockResolvedValue(book({ ready_generation: 2 }))
    await flush(10_000)
    await flush()
    expect(api.getSentences).toHaveBeenLastCalledWith('b1', 2)
    expect(screen.queryByText(/New version ready/)).toBeNull()
  })

  it('polls getBook on window focus', async () => {
    vi.mocked(api.getBook).mockResolvedValue(book({ ready_generation: 1 }))
    render(ReaderPage)
    await flush()
    const n = vi.mocked(api.getBook).mock.calls.length
    window.dispatchEvent(new Event('focus'))
    await flush()
    expect(vi.mocked(api.getBook).mock.calls.length).toBe(n + 1)
  })
})
