import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { get } from 'svelte/store'

const { socketCtor } = vi.hoisted(() => ({ socketCtor: vi.fn() }))
vi.mock('$lib/api', () => ({
  TTSSocket: class { constructor() { socketCtor() } },
  renderAudioUrl: (b: string, g: number, t: string, f = 'opus') => `/api/renders/${b}/${g}/audio_${t}.${f}`,
}))

import { audioStore, speedRevertStore } from '$lib/stores/audio'
import { toastStore } from '$lib/stores/toast'

const log: string[] = []
const made: FakeAudio[] = []
let canPlay: string | ((mime: string) => string) = 'maybe'

class FakeAudio {
  src: string
  currentTime = 0
  volume = 1
  muted = false
  duration = 1000
  ended = false
  paused = true
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
  canPlayType(mime = '') { return typeof canPlay === 'function' ? canPlay(mime) : canPlay }
  play() { log.push(`play:${this.src}`); this.paused = false; return Promise.resolve() }
  pause() { log.push(`pause:${this.src}`); this.paused = true }
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
const url = (t: string) => `/api/renders/b/1/audio_${t}.opus`
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

  it('seeking while playing into buffered audio does not pause', async () => {
    setup()
    const e = (audioStore.play(0), el('1.0'))
    e.bufferedEnd = 5
    await flush()
    log.length = 0
    audioStore.seek(1)
    expect(e.currentTime).toBeCloseTo(1.1)
    expect(get(audioStore).currentIndex).toBe(1)
    await flush()
    expect(log).toEqual([])
    expect(e.paused).toBe(false)
    expect(get(audioStore).isPlaying).toBe(true)
  })

  it('seeking into unbuffered audio keeps playing and shows buffering', async () => {
    setup()
    const e = (audioStore.play(0), el('1.0'))
    e.bufferedEnd = 0.5
    await flush()
    log.length = 0
    audioStore.seek(1)
    expect(log.some(l => l.startsWith('pause'))).toBe(false)
    expect(get(audioStore).buffering).toBe(true)
    expect(get(audioStore).isPlaying).toBe(true)
    e.emit('playing')
    expect(get(audioStore).buffering).toBe(false)
  })

  it('rapid seeks: the last click wins and the highlight does not snap back', async () => {
    setup()
    const e = (audioStore.play(0), el('1.0'))
    e.bufferedEnd = 5
    await flush()
    audioStore.seek(1)
    audioStore.seek(0)
    audioStore.seek(1)
    tickAll()
    expect(get(audioStore).currentIndex).toBe(1)
    expect(e.currentTime).toBeCloseTo(1.1)
  })

  it('seeking while paused gates then plays', async () => {
    setup()
    const e = (audioStore.play(0), el('1.0'))
    e.bufferedEnd = 5
    await flush()
    audioStore.pause()
    log.length = 0
    audioStore.seek(1)
    await flush()
    expect(log.map(l => l.split(':')[0])).toEqual(['pause', 'play'])
  })

  it('play from the sentence the pause is in resumes without a seek or replay', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6 // inside sentence 0
    tickAll()
    audioStore.pause()
    log.length = 0
    audioStore.play(0)
    await flush()
    expect(el('1.0').currentTime).toBe(0.6)
    expect(log.some(l => l.startsWith('pause'))).toBe(false)
    expect(log).toContain(`play:${url('1.0')}`)
  })

  it('play from a different sentence still seeks to its start', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    audioStore.pause()
    audioStore.play(1)
    expect(el('1.0').currentTime).toBeCloseTo(1.1)
  })

  it('tier swap lands on the exact playhead (scaled) with zero socket calls', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    tickAll()
    expect(get(audioStore).currentWordIndex).toBe(1)
    audioStore.setSpeed(2)
    const fast = el('2.0')
    fast.bufferedEnd = 5
    await flush()
    expect(fast.currentTime).toBeCloseTo(0.3) // 0.6 s into a 1x file is 0.3 s into the 2x file
    expect(log).toContain(`pause:${url('1.0')}`)
    expect(socketCtor).not.toHaveBeenCalled()
  })

  it('reads the playhead from the element clock even when no frame ran (hidden tab)', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.9 // no rAF tick: the mirror still says 0
    audioStore.setSpeed(2)
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('2.0').currentTime).toBeCloseTo(0.45)
  })

  it('does not seek again when the prewarmed element already sits on the target', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    audioStore.setSpeed(2)
    const fast = el('2.0') as any
    let writes = 0
    let t = fast.currentTime
    Object.defineProperty(fast, 'currentTime', { get: () => t, set: v => { writes++; t = v } })
    fast.readyState = 4
    fast.bufferedEnd = 5
    await flush()
    expect(el('1.0').paused).toBe(true)
    expect(writes).toBe(0)
  })

  it('abandons a swap whose tier never loads: old tier keeps playing, speed reverts, user told', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    const reverts: (number | null)[] = []
    const un = speedRevertStore.subscribe(v => reverts.push(v))
    audioStore.setSpeed(2)
    await vi.advanceTimersByTimeAsync(13000) // el('2.0') never buffers
    expect(el('1.0').paused).toBe(false)
    expect(get(audioStore).speed).toBe(1)
    expect(reverts).toContain(1)
    expect(get(toastStore).some(t => /Could not switch to 2x/.test(t.title))).toBe(true)
    un()
  })

  it('an element error abandons the swap at once', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    ;(el('2.0') as any).error = { code: 4 }
    await flush()
    expect(el('1.0').paused).toBe(false)
    expect(get(audioStore).speed).toBe(1)
  })

  it('a sentence click on the sentence you are paused in restarts it', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    audioStore.pause()
    audioStore.play(0, true)
    expect(el('1.0').currentTime).toBe(0)
  })

  it('the old tier keeps sounding until the new one is buffered', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    log.length = 0
    audioStore.setSpeed(2)
    await flush()
    expect(log).toEqual([])
    expect(el('1.0').paused).toBe(false)
    expect(get(audioStore).speed).toBe(2)
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('1.0').paused).toBe(true)
    expect(log).toContain(`play:${url('2.0')}`)
  })

  it('pausing during a pending swap lands on the chosen tier, paused', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    audioStore.pause()
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('1.0').paused).toBe(true)
    expect(el('2.0').paused).toBe(true)
    expect(get(audioStore).isPlaying).toBe(false)
  })

  it('switching back before the new tier is ready cancels the swap', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    audioStore.setSpeed(1)
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('1.0').paused).toBe(false)
    expect(el('2.0').paused).toBe(true)
  })

  it('swap seeks to sentence start when the sentence has no word times', async () => {
    setup('aligned', true)
    audioStore.play(1)
    el('1.0').bufferedEnd = 5
    await flush()
    audioStore.setSpeed(2)
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('2.0').currentTime).toBeCloseTo(0.55)
  })

  it('rAF ignores the old element during a swap', async () => {
    setup()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    const stale = rafQueue.splice(0)
    audioStore.setSpeed(2)
    el('2.0').bufferedEnd = 5
    await flush()
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

describe('audio container', () => {
  const withFormats = (formats?: string[]) => ({ ...manifest(), ...(formats ? { audio_formats: formats } : {}) })
  const start = (m: any) => { audioStore.init('b'); audioStore.load(1, m); audioStore.play(0) }

  it('plays WebM when the render offers it and the browser can decode it', () => {
    start(withFormats(['webm', 'opus']))
    expect(made.some(m => m.src === '/api/renders/b/1/audio_1.0.webm')).toBe(true)
    expect(get(audioStore).unsupportedMessage).toBeNull()
  })

  it('keeps Ogg when the render offers nothing else', () => {
    start(withFormats())
    expect(made.some(m => m.src === '/api/renders/b/1/audio_1.0.opus')).toBe(true)
  })

  it('falls back to Ogg when the browser cannot decode WebM', () => {
    canPlay = (mime: string) => (mime.includes('webm') ? '' : 'maybe')
    start(withFormats(['webm', 'opus']))
    expect(made.some(m => m.src === '/api/renders/b/1/audio_1.0.opus')).toBe(true)
  })

  it('uses WebM alone when only WebM is playable (an Ogg-less browser)', () => {
    canPlay = (mime: string) => (mime.includes('ogg') ? '' : 'maybe')
    start(withFormats(['webm', 'opus']))
    expect(made.some(m => m.src === '/api/renders/b/1/audio_1.0.webm')).toBe(true)
    expect(get(audioStore).unsupportedMessage).toBeNull()
  })

  it('an Ogg-only render in a browser without Ogg is unsupported', () => {
    canPlay = (mime: string) => (mime.includes('ogg') ? '' : 'maybe')
    start(withFormats())
    expect(get(audioStore).unsupportedMessage).toMatch(/Opus/)
  })
})

describe('speeds come from the manifest', () => {
  function withTiers(keys: string[]): any {
    const m = manifest()
    const one = m.tiers['1.0']
    m.tiers = Object.fromEntries(keys.map(k => [k, { ...one, requested_factor: Number(k), actual_factor: Number(k) }]))
    return m
  }

  it('offers exactly the tiers of a new render', () => {
    audioStore.init('b')
    audioStore.load(1, withTiers(['1.0', '1.5', '2.0', '2.5', '3.0']))
    expect(get(audioStore).availableSpeeds).toEqual([1, 1.5, 2, 2.5, 3])
  })

  it('offers every tier an old render still has', () => {
    audioStore.init('b')
    audioStore.load(1, withTiers(['0.75', '1.0', '1.25', '1.5', '1.75', '2.0', '2.5', '3.0']))
    expect(get(audioStore).availableSpeeds).toEqual([0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3])
  })

  it('snaps a speed the render lacks to the nearest tier and says which', () => {
    audioStore.init('b')
    audioStore.load(1, withTiers(['1.0', '1.5', '2.0', '2.5', '3.0']))
    expect(audioStore.setSpeed(0.75)).toBe(1)
    expect(get(audioStore).speed).toBe(1)
    expect(audioStore.setSpeed(2.6)).toBe(2.5)
    expect(get(audioStore).speed).toBe(2.5)
  })

  it('forgets the speeds when the book closes', () => {
    audioStore.init('b')
    audioStore.load(1, withTiers(['1.0', '2.0']))
    audioStore.destroy()
    expect(get(audioStore).availableSpeeds).toEqual([])
  })
})

describe('slim manifest (schema 2): one 1x timeline, tiers divide', () => {
  // A real measured factor: tier times are 1x / actual_factor, never 1x / requested.
  const A2 = 1.9999999874080308
  function slimManifest(quality = 'aligned'): any {
    const segments = [
      seg(0, 0, 1000, [word(0, 400), word(500, 1000)]),
      seg(1, 1100, 2000, [word(1100, 1500), word(1500, 2000)]),
      seg(4, 2100, 3000, [word(2100, 3000)]),
    ]
    return { schema: 2, duration_ms: 3000, base_effective_speed: 1, voice: 'v', fingerprint: 'f',
      container: 'ogg-opus', timing_quality: quality, mixed_backends: false, segments,
      tiers: { '1.0': { requested_factor: 1, actual_factor: 1, max_drift_ms: 0 },
        '2.0': { requested_factor: 2, actual_factor: A2, max_drift_ms: 0 } } }
  }
  function setupSlim() {
    audioStore.init('b')
    audioStore.load(1, slimManifest())
  }

  it('seeks on the tier by 1x / actual_factor', async () => {
    setupSlim()
    audioStore.setSpeed(2)
    const e = (audioStore.play(4), el('2.0'))
    expect(e.currentTime).toBe(2100 / A2 / 1000)
    e.bufferedEnd = 5
    await flush()
    expect(get(audioStore).sentenceDurations[1]).toBe((2000 / A2 - 1100 / A2) / 1000)
  })

  it('maps the element clock to sentence and word on a derived tier', async () => {
    setupSlim()
    audioStore.setSpeed(2)
    audioStore.play(0)
    const e = el('2.0')
    e.bufferedEnd = 5
    await flush()
    e.currentTime = (1500 / A2 + 1) / 1000
    tickAll()
    expect(get(audioStore).currentIndex).toBe(1)
    expect(get(audioStore).currentWordIndex).toBe(1)
    e.currentTime = (2100 / A2) / 1000 // exactly the start of index 4
    tickAll()
    expect(get(audioStore).currentIndex).toBe(4)
  })

  it('a frame that changes nothing does not notify subscribers', async () => {
    setupSlim()
    audioStore.play(0)
    const e = el('1.0')
    e.bufferedEnd = 5
    await flush()
    e.currentTime = 0.2
    tickAll()
    let calls = 0
    const unsub = audioStore.subscribe(() => { calls++ })
    calls = 0
    for (let i = 0; i < 30; i++) tickAll()
    expect(calls).toBe(0)
    e.currentTime = 1.2
    tickAll()
    expect(calls).toBe(1)
    unsub()
  })

  it('switching tiers keeps the exact playhead via the 1x timeline', async () => {
    setupSlim()
    audioStore.play(0)
    el('1.0').bufferedEnd = 5
    await flush()
    el('1.0').currentTime = 0.6
    tickAll()
    audioStore.setSpeed(2)
    el('2.0').bufferedEnd = 5
    await flush()
    expect(el('2.0').currentTime).toBeCloseTo(0.6 / A2, 6)
  })

  it('keeps the manifest arrays as they are (no copies of the timeline)', () => {
    const m = slimManifest()
    audioStore.init('b')
    audioStore.load(1, m)
    expect(Object.keys(m.tiers['2.0'])).not.toContain('segments')
  })
})
