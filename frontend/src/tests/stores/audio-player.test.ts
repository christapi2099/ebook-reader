import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { get } from 'svelte/store'

const { socketCtor } = vi.hoisted(() => ({ socketCtor: vi.fn() }))
vi.mock('$lib/api', () => ({
  TTSSocket: class { constructor() { socketCtor() } },
  renderAudioUrl: (b: string, g: number, t: string) => `/renders/${b}/${g}/audio_${t}.opus`,
}))

import { audioStore } from '$lib/stores/audio'

const log: string[] = []
const made: FakeAudio[] = []
let canPlay = 'maybe'

class FakeAudio {
  src: string
  currentTime = 0
  volume = 1
  muted = false
  duration = 1000
  ended = false
  preload = ''
  bufferedEnd = 0
  listeners: Record<string, (() => void)[]> = {}
  constructor(src = '') { this.src = src; made.push(this) }
  get buffered() {
    const e = this.bufferedEnd
    return { length: e > 0 ? 1 : 0, start: () => 0, end: () => e }
  }
  addEventListener(n: string, f: () => void) { (this.listeners[n] ??= []).push(f) }
  emit(n: string) { (this.listeners[n] ?? []).forEach(f => f()) }
  canPlayType() { return canPlay }
  play() { log.push(`play:${this.src}`); return Promise.resolve() }
  pause() { log.push(`pause:${this.src}`) }
  removeAttribute() {}
  load() {}
}

const word = (s: number, e: number) => ({ start_ms: s, end_ms: e, box: null, timing_source: 'aligned' })
const seg = (index: number, s: number, e: number, words: any[] = []) => ({ index, start_ms: s, end_ms: e, gap_ms: 100, words })
function manifest(quality = 'aligned', noWords = false): any {
  const mk = (k: number) => ({
    requested_factor: k, actual_factor: k, max_drift_ms: 0,
    segments: [
      seg(0, 0, 1000 / k, noWords ? [] : [word(0, 400 / k), word(500 / k, 1000 / k)]),
      seg(1, 1100 / k, 2000 / k, noWords ? [] : [word(1100 / k, 1500 / k), word(1500 / k, 2000 / k)]),
    ],
  })
  return { duration_ms: 2000, base_effective_speed: 1, voice: 'v', fingerprint: 'f', container: 'opus',
    timing_quality: quality, mixed_backends: false, tiers: { '1.0': mk(1), '2.0': mk(2) } }
}

let rafQueue: (() => void)[] = []
const url = (t: string) => `/renders/b/1/audio_${t}.opus`
const el = (t: string) => made.find(m => m.src === url(t))!
const flush = () => vi.advanceTimersByTimeAsync(100)

beforeEach(() => {
  vi.useFakeTimers()
  log.length = 0; made.length = 0; rafQueue = []; canPlay = 'maybe'; socketCtor.mockClear()
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('requestAnimationFrame', (f: () => void) => { rafQueue.push(f); return rafQueue.length })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  const dec = vi.fn()
  vi.stubGlobal('AudioContext', class { decodeAudioData = dec })
  ;(globalThis as any).__dec = dec
  audioStore.destroy()
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function setup(quality = 'aligned', noWords = false) {
  audioStore.init('b')
  audioStore.load(1, manifest(quality, noWords))
}
const tickAll = () => { const q = rafQueue; rafQueue = []; q.forEach(f => f()) }

describe('render tier player', () => {
  it('gates start on 300 ms buffered and follows waiting/playing', async () => {
    setup()
    audioStore.play(0)
    await flush()
    expect(log.filter(l => l === `play:${url('1.0')}`).length).toBe(1) // only the prime play
    expect(get(audioStore).buffering).toBe(true)
    el('1.0').bufferedEnd = 0.2
    await flush()
    expect(log.filter(l => l === `play:${url('1.0')}`).length).toBe(1)
    el('1.0').bufferedEnd = 0.4
    await flush()
    expect(log.filter(l => l === `play:${url('1.0')}`).length).toBe(2)
    el('1.0').emit('playing')
    expect(get(audioStore).buffering).toBe(false)
    el('1.0').emit('waiting')
    expect(get(audioStore).buffering).toBe(true)
  })

  it('pauses before seeking before playing on click', async () => {
    setup()
    const e = (audioStore.play(0), el('1.0'))
    e.bufferedEnd = 5
    await flush()
    log.length = 0
    let seeked = -1
    Object.defineProperty(e, 'currentTime', {
      get: () => 0, set: (v: number) => { seeked = v; log.push('seek') }, configurable: true,
    })
    audioStore.seek(1)
    await flush()
    expect(seeked).toBeCloseTo(1.1)
    expect(log.map(l => l.split(':')[0]).slice(0, 3)).toEqual(['pause', 'seek', 'play'])
    expect(get(audioStore).currentIndex).toBe(1)
  })

  it('tier swap seeks to the current word start with zero socket calls', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    tickAll()
    expect(get(audioStore).currentWordIndex).toBe(1)
    audioStore.setSpeed(2)
    const fast = el('2.0')
    expect(fast.currentTime).toBeCloseTo(0.25) // word 1 starts 500/2 ms
    expect(log).toContain(`pause:${url('1.0')}`)
    expect(socketCtor).not.toHaveBeenCalled()
  })

  it('swap seeks to sentence start when the sentence has no word times', async () => {
    setup('aligned', true)
    audioStore.play(1)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    expect(el('2.0').currentTime).toBeCloseTo(0.55)
  })

  it('rAF ignores the old element during a swap', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    const stale = rafQueue.splice(0)
    audioStore.setSpeed(2)
    el('1.0').currentTime = 1.5 // would map to sentence 1
    stale.forEach(f => f())
    expect(get(audioStore).currentIndex).toBe(0)
  })

  it('estimated timing gives sentence-only highlight', async () => {
    setup('estimated')
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    tickAll()
    expect(get(audioStore).currentIndex).toBe(0)
    expect(get(audioStore).currentWordIndex).toBe(-1)
  })

  it('advances by time across a gap and stops at the end of the book', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 1.05 // in the gap after sentence 0
    tickAll()
    expect(get(audioStore).currentIndex).toBe(0)
    expect(get(audioStore).currentWordIndex).toBe(-1)
    el('1.0').currentTime = 1.2
    tickAll()
    expect(get(audioStore).currentIndex).toBe(1)
    el('1.0').currentTime = 2.1
    el('1.0').ended = true
    tickAll()
    expect(get(audioStore).isPlaying).toBe(false)
  })

  it('never calls decodeAudioData', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    expect((globalThis as any).__dec).not.toHaveBeenCalled()
  })

  it('shows an unsupported message when Opus is not playable', () => {
    canPlay = ''
    setup()
    expect(get(audioStore).unsupportedMessage).toMatch(/Opus/)
    audioStore.play(0)
    expect(made.filter(m => m.src).length).toBe(0)
  })

  it('destroy pauses and drops every element', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    log.length = 0
    audioStore.destroy()
    expect(log).toContain(`pause:${url('1.0')}`)
    audioStore.play(0)
    expect(get(audioStore).isPlaying).toBe(false)
  })
})
