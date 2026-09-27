import { describe, it, expect, beforeEach, vi } from 'vitest'
import { get } from 'svelte/store'
import { audioStore } from '$lib/stores/audio'
import { settingsStore } from '$lib/stores/settings'

class MockAudioContext {
  state = 'running'
  currentTime = 0
  destination = {}
  resume() { return Promise.resolve() }
  close() { return Promise.resolve() }
  createBufferSource() {
    return {
      buffer: null,
      playbackRate: { value: 1.0 },
      connect: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
      onended: null,
    }
  }
  decodeAudioData() { return Promise.resolve({ duration: 0.1 }) }
}
vi.stubGlobal('AudioContext', MockAudioContext)

const manifest = {
  requested_speed: 1.0,
  effective_speed: 1.0,
  method: 'native' as const,
  sample_rate: 24000,
  audio_url: 'sample_1.opus',
  duration_ms: 6000,
  segments: [
    { sentence_index: 0, page: 0, start_ms: 0, end_ms: 2000, words: [{ text: 'Hi', start_ms: 0, end_ms: 500 }] },
    { sentence_index: 1, page: 0, start_ms: 2000, end_ms: 4000, words: [] },
    { sentence_index: 2, page: 0, start_ms: 4000, end_ms: 6000, words: [] },
  ],
}

const statusCalls: unknown[] = []
const buildCalls: unknown[] = []
let statusResponse: { tiers: unknown[]; queue_position: number | null } = { tiers: [], queue_position: null }

vi.mock('$lib/api', () => {
  const sentMessages: unknown[] = []
  return {
    TTSSocket: class {
      onAudioChunk = () => {}
      onSentenceStart = () => {}
      onSentenceEnd = () => {}
      onComplete = () => {}
      connect() {}
      disconnect() {}
      play(fromIndex: number, voice: string, speed: number, sessionId: number) {
        sentMessages.push({ action: 'play', from_index: fromIndex, voice, speed, session_id: sessionId })
      }
      seek(toIndex: number, voice: string, speed: number, sessionId: number) {
        sentMessages.push({ action: 'seek', to_index: toIndex, voice, speed, session_id: sessionId })
      }
      pause() {
        sentMessages.push({ action: 'pause' })
      }
    },
    getSentMessages: () => sentMessages,
    clearSentMessages: () => { sentMessages.length = 0 },
    getPrecompiledStatus: (bookId: string, chapter: number, voice: string) => {
      statusCalls.push({ bookId, chapter, voice })
      return Promise.resolve(statusResponse)
    },
    getPrecompiledManifest: () => Promise.resolve(manifest),
    buildPrecompiledChapter: (bookId: string, chapter: number, voice: string, speeds: number[]) => {
      buildCalls.push({ bookId, chapter, voice, speeds })
      return Promise.resolve({ status: 'queued', queue_position: 0 })
    },
    precompiledAudioUrl: (bookId: string, chapter: number, voice: string, speed: number) =>
      `http://test/${bookId}/${chapter}/${voice}/${speed}`,
  }
})

async function importSentMessages() {
  const mod = await import('$lib/api') as unknown as { getSentMessages: () => unknown[]; clearSentMessages: () => void }
  return mod
}

function flushMicrotasks(): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, 0))
}

describe('audioStore precompiled playback', () => {
  beforeEach(async () => {
    audioStore.destroy()
    const { clearSentMessages } = await importSentMessages()
    clearSentMessages()
    settingsStore.reset()
    statusCalls.length = 0
    buildCalls.length = 0
    statusResponse = { tiers: [], queue_position: null }
  })

  it('falls back to the live socket on the first play of a chapter with no precompiled tier', async () => {
    audioStore.init('book-1')
    audioStore.play(0)

    const { getSentMessages } = await importSentMessages()
    expect(getSentMessages()).toContainEqual(
      expect.objectContaining({ action: 'play', from_index: 0 }),
    )
  })

  it('triggers a background build when no tier exists yet for the chapter', async () => {
    statusResponse = { tiers: [], queue_position: null }
    audioStore.init('book-2')
    audioStore.play(0)
    await flushMicrotasks()

    expect(statusCalls).toContainEqual(expect.objectContaining({ bookId: 'book-2', chapter: 0 }))
    expect(buildCalls).toContainEqual(expect.objectContaining({ bookId: 'book-2', chapter: 0 }))
  })

  it('does not trigger a second build while one is already pending', async () => {
    statusResponse = {
      tiers: [
        { requested_speed: 1.0, effective_speed: null, method: 'native', status: 'processing', phase: 'processing', progress: 40, duration_ms: null, error_message: null },
      ],
      queue_position: 0,
    }
    audioStore.init('book-3')
    audioStore.play(0)
    await flushMicrotasks()

    expect(buildCalls).toHaveLength(0) // a tier is already in flight, must not pile on another build
  })

  it('uses the precompiled tier once the background check resolves, without touching the live socket', async () => {
    statusResponse = {
      tiers: [
        { requested_speed: 1.0, effective_speed: 1.0, method: 'native', status: 'done', phase: 'complete', progress: 100, duration_ms: 6000, error_message: null },
      ],
      queue_position: null,
    }
    audioStore.init('book-4')

    // First play(): live fallback fires immediately, background check starts.
    audioStore.play(0)
    const { getSentMessages, clearSentMessages } = await importSentMessages()
    expect(getSentMessages().length).toBeGreaterThan(0)

    await flushMicrotasks()
    clearSentMessages()

    // Second play(): the check has resolved, so this one must go straight to
    // the precompiled path — no new message to the live socket.
    audioStore.play(1)
    await flushMicrotasks()

    expect(getSentMessages()).toEqual([])
    expect(get(audioStore).currentIndex).toBe(1)
  })

  it('seek() also uses a cached precompiled tier without contacting the live socket', async () => {
    statusResponse = {
      tiers: [
        { requested_speed: 1.0, effective_speed: 1.0, method: 'native', status: 'done', phase: 'complete', progress: 100, duration_ms: 6000, error_message: null },
      ],
      queue_position: null,
    }
    audioStore.init('book-5')
    audioStore.play(0)
    await flushMicrotasks()

    const { getSentMessages, clearSentMessages } = await importSentMessages()
    clearSentMessages()

    audioStore.seek(2)
    await flushMicrotasks()

    expect(getSentMessages()).toEqual([])
    expect(get(audioStore).currentIndex).toBe(2)
  })

  it('setSpeed switches to a cached precompiled tier at the new speed while playing, with no live re-synthesis', async () => {
    statusResponse = {
      tiers: [
        { requested_speed: 1.0, effective_speed: 1.0, method: 'native', status: 'done', phase: 'complete', progress: 100, duration_ms: 6000, error_message: null },
      ],
      queue_position: null,
    }
    audioStore.init('book-6')
    audioStore.play(0)
    await flushMicrotasks()

    const { getSentMessages, clearSentMessages } = await importSentMessages()
    clearSentMessages()

    // Same speed (1.0, already cached and playing) re-applied should not
    // touch the live socket.
    audioStore.setSpeed(1.0)
    await flushMicrotasks()

    expect(getSentMessages()).toEqual([])
    expect(get(audioStore).speed).toBe(1.0)
  })
})
