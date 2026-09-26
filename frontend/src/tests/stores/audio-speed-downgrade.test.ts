import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { get } from 'svelte/store'

/**
 * The socket is mocked so the test can deliver the backend's
 * `speed_unavailable` message through the store's real handler — the wiring
 * under test is exactly the one `init()` installs.
 */
const { sockets, sent } = vi.hoisted(() => ({
  sockets: [] as any[],
  sent: [] as any[],
}))

vi.mock('$lib/api', () => ({
  TTSSocket: class {
    onAudioChunk = () => {}
    onSentenceStart = () => {}
    onSentenceEnd = () => {}
    onComplete = () => {}
    onSpeedUnavailable: ((requested: number, effective: number, sessionId: number) => void) | undefined
    constructor() {
      sockets.push(this)
    }
    connect() {}
    disconnect() {}
    play(fromIndex: number, voice: string, speed: number, sessionId: number) {
      sent.push({ action: 'play', from_index: fromIndex, voice, speed, session_id: sessionId })
    }
    seek(toIndex: number, voice: string, speed: number, sessionId: number) {
      sent.push({ action: 'seek', to_index: toIndex, voice, speed, session_id: sessionId })
    }
    prefetchSpeed(fromIndex: number, voice: string, speed: number) {
      sent.push({ action: 'prefetch_speed', from_index: fromIndex, voice, speed })
    }
    pause() {
      sent.push({ action: 'pause' })
    }
  },
}))

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

import { audioStore, speedDowngradeStore } from '$lib/stores/audio'

/** The socket `init()` built, i.e. the one the store listens to. */
function currentSocket(): any {
  return sockets[sockets.length - 1]
}

/** The session id the store actually sent: it counts up across sessions. */
function lastPlaySessionId(): number {
  return sent.filter(m => m.action === 'play').at(-1)!.session_id
}

describe('audioStore speed downgrade', () => {
  let sessionId = 0

  beforeEach(() => {
    sockets.length = 0
    sent.length = 0
    speedDowngradeStore.set(null)
    audioStore.destroy()
    audioStore.init('book-1')
    // One play() opens the session every later message has to carry.
    audioStore.play(0)
    sessionId = lastPlaySessionId()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts with no downgrade recorded', () => {
    expect(get(speedDowngradeStore)).toBeNull()
  })

  it('forces the store speed to the rate the engine really used', () => {
    audioStore.setSpeed(1.5)
    expect(get(audioStore).speed).toBe(1.5)

    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId)

    expect(get(audioStore).speed).toBe(1.0)
    expect(get(speedDowngradeStore)).toEqual({ requested: 1.5, effective: 1.0 })
  })

  it('ignores a downgrade from a session that is no longer current', () => {
    audioStore.setSpeed(1.5)
    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId + 99)

    expect(get(audioStore).speed).toBe(1.5)
    expect(get(speedDowngradeStore)).toBeNull()
  })

  it('ignores a downgrade carrying no session tag from another session', () => {
    audioStore.setSpeed(2.0)
    // sid 0 is what a message without a usable session_id is parsed as.
    currentSocket().onSpeedUnavailable(2.0, 1.0, 0)

    expect(get(audioStore).speed).toBe(2.0)
    expect(get(speedDowngradeStore)).toBeNull()
  })

  it('drops durations that no longer describe the audio being played', () => {
    audioStore.setSpeed(1.5)
    currentSocket().onSentenceEnd(0, 1500, sessionId)
    expect(get(audioStore).sentenceDurations).toEqual({ 0: 1.5 })

    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId)

    expect(get(audioStore).sentenceDurations).toEqual({})
    expect(get(audioStore).elapsedSeconds).toBe(0)
  })

  it('keeps durations when the effective rate is the one already in use', () => {
    currentSocket().onSentenceEnd(0, 1000, sessionId)
    expect(get(audioStore).sentenceDurations).toEqual({ 0: 1 })

    currentSocket().onSpeedUnavailable(1.25, 1.0, sessionId)

    expect(get(audioStore).speed).toBe(1.0)
    expect(get(audioStore).sentenceDurations).toEqual({ 0: 1 })
    expect(get(speedDowngradeStore)).toEqual({ requested: 1.25, effective: 1.0 })
  })

  it('cancels a queued speed change instead of re-requesting the refused rate', () => {
    vi.useFakeTimers()
    sent.length = 0

    audioStore.setSpeed(1.5)
    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId)
    vi.advanceTimersByTime(1000)

    expect(sent.filter(m => m.action === 'play')).toHaveLength(0)
    expect(get(audioStore).speed).toBe(1.0)
  })

  it('clears the notice for a new connection', () => {
    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId)
    expect(get(speedDowngradeStore)).not.toBeNull()

    audioStore.init('book-2')

    expect(get(speedDowngradeStore)).toBeNull()
  })

  it('clears the notice on destroy', () => {
    currentSocket().onSpeedUnavailable(1.5, 1.0, sessionId)
    expect(get(speedDowngradeStore)).not.toBeNull()

    audioStore.destroy()

    expect(get(speedDowngradeStore)).toBeNull()
  })
})
