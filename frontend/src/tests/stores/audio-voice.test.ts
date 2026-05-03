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
  }
})

async function importSentMessages() {
  const mod = await import('$lib/api') as { getSentMessages: () => unknown[]; clearSentMessages: () => void }
  return mod
}

describe('audioStore voice propagation', () => {
  beforeEach(async () => {
    audioStore.destroy()
    const { clearSentMessages } = await importSentMessages()
    clearSentMessages()
    settingsStore.reset()
  })

  it('defaults voice to af_heart', () => {
    expect(get(audioStore).voice).toBe('af_heart')
  })

  it('setVoice updates the store voice', () => {
    audioStore.setVoice('am_adam')
    expect(get(audioStore).voice).toBe('am_adam')
  })

  it('play() sends the current voice to the socket', async () => {
    const { getSentMessages } = await importSentMessages()
    audioStore.init('test-book')
    audioStore.setVoice('bf_emma')
    audioStore.play(0)

    const msgs = getSentMessages()
    const playMsg = msgs.find((m: any) => m.action === 'play') as any
    expect(playMsg).toBeDefined()
    expect(playMsg.voice).toBe('bf_emma')
  })

  it('play() sends updated voice after voice change', async () => {
    const { getSentMessages, clearSentMessages } = await importSentMessages()
    audioStore.init('test-book')

    audioStore.setVoice('af_heart')
    audioStore.play(0)
    let playMsg = (getSentMessages() as any[]).find(m => m.action === 'play')
    expect(playMsg.voice).toBe('af_heart')

    clearSentMessages()
    audioStore.setVoice('am_michael')
    audioStore.play(5)
    playMsg = (getSentMessages() as any[]).find(m => m.action === 'play')
    expect(playMsg.voice).toBe('am_michael')
  })

  it('seek() sends the current voice to the socket', async () => {
    const { getSentMessages } = await importSentMessages()
    audioStore.init('test-book')
    audioStore.setVoice('af_nicole')
    audioStore.seek(3)

    const msgs = getSentMessages()
    const seekMsg = msgs.find((m: any) => m.action === 'seek') as any
    expect(seekMsg).toBeDefined()
    expect(seekMsg.voice).toBe('af_nicole')
  })

  it('settingsStore voice change syncs to audioStore when wired', () => {
    audioStore.setVoice(get(settingsStore).voice)
    expect(get(audioStore).voice).toBe('af_heart')

    settingsStore.setVoice('am_adam')
    audioStore.setVoice(get(settingsStore).voice)
    expect(get(audioStore).voice).toBe('am_adam')
  })
})
