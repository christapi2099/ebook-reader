import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/svelte'
import { readable, get } from 'svelte/store'
import type { Book } from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))
vi.mock('$app/stores', () => ({ page: readable({ params: { id: 'b1' } }) }))
vi.mock('$lib/components/TextViewer.svelte', async () => ({ default: (await import('./stubs/Stub.svelte')).default }))
vi.mock('$lib/components/PDFViewer.svelte', async () => ({ default: (await import('./stubs/SentencesProbe.svelte')).default }))
vi.mock('$lib/stores/audio', async () => {
  const { writable } = await import('svelte/store')
  let state: Record<string, unknown> = {
    isPlaying: false, currentIndex: 0, currentWordIndex: -1, buffering: false,
    elapsedSeconds: 0, sentenceDurations: {}, availableSpeeds: [1],
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
import ReaderPage from '../../routes/reader/[id]/+page.svelte'

const sent = (n: number) => Array.from({ length: n }, (_, i) => ({
  index: i, text: `s${i}`, page: Math.floor(i / 10), x0: 0, y0: 0, x1: 0, y1: 0, filtered: false, chapter: 0, words: [],
}))
const setAudio = (p: object) => (audioStore as unknown as { __set: (p: object) => void }).__set(p)
const manifest = { voice: 'af_heart', tiers: {}, segments: [] } as never
async function settle() { for (let i = 0; i < 100; i++) await vi.advanceTimersByTimeAsync(0) }

describe('reader page keeps the big arrays out of reactive proxies', () => {
  beforeEach(() => {
    cleanup(); vi.useFakeTimers()
    ;(globalThis as any).__probe = { recomputes: 0, last: null }
    setAudio({ isPlaying: false, currentIndex: 0, elapsedSeconds: 0 })
    vi.resetAllMocks()
    vi.mocked(api.getBook).mockResolvedValue({ id: 'b1', title: 'T', author: null, file_type: 'pdf',
      page_count: 3, folder_id: null, ready_generation: 1 } as Book)
    vi.mocked(api.getSentences).mockResolvedValue(sent(30) as never)
    vi.mocked(api.getProgress).mockResolvedValue(0)
    vi.mocked(api.getRenderManifest).mockResolvedValue(manifest)
    vi.mocked(api.getVoices).mockResolvedValue([])
    vi.mocked(api.getUserSettings).mockResolvedValue({} as never)
    vi.mocked(api.saveProgress).mockResolvedValue(undefined as never)
    vi.mocked(api.updateUserSettings).mockResolvedValue({} as never)
  })
  afterEach(() => { vi.useRealTimers() })

  it('the viewer gets the store array itself, and audio frames do not re-run its sentence work', async () => {
    render(ReaderPage)
    await settle()
    const probe = (globalThis as any).__probe
    expect(probe.last).toBe(get(readerStore).sentences) // not a $state proxy of it
    const before = probe.recomputes
    setAudio({ isPlaying: true })
    for (let i = 0; i < 40; i++) {
      setAudio({ elapsedSeconds: i * 0.1, currentIndex: Math.floor(i / 4), currentWordIndex: i % 4 })
      await vi.advanceTimersByTimeAsync(0)
    }
    expect(get(readerStore).currentIndex).toBe(9) // the page did follow playback
    expect(probe.recomputes).toBe(before)
  })
})
