import { writable, get } from 'svelte/store'
import { renderAudioUrl, type RenderManifest } from '$lib/api'
import { nearestSpeed } from '$lib/utils/speed-tiers'
import {
  durationsOf,
  normalizeManifest,
  positionOf,
  segEndMs,
  segmentAt,
  segStartMs,
  tierTimeline,
  wordStartMs,
  type Timeline,
} from '$lib/utils/manifest-timeline'

export interface AudioState {
  isPlaying: boolean
  /** The tier being played (snapped to one the loaded render has). */
  speed: number
  /** Speeds the loaded render offers: its manifest tier factors, ascending. */
  availableSpeeds: number[]
  currentIndex: number
  currentWordIndex: number
  voice: string
  buffering: boolean
  /** Real seconds of the active tier played so far (the active element's clock). */
  elapsedSeconds: number
  /** Real per-sentence durations in seconds of the active tier, keyed by sentence index. */
  sentenceDurations: Record<number, number>
  /** Non-null when this browser cannot play the Opus render; shown instead of a dead player. */
  unsupportedMessage: string | null
}

/**
 * A rate the engine refused. Render-based playback never downgrades (every tier
 * is pre-rendered), so nothing sets this any more; it stays because the MediaBar
 * reads it.
 */
export interface SpeedDowngrade {
  requested: number
  effective: number
}
export const speedDowngradeStore = writable<SpeedDowngrade | null>(null)

export const UNSUPPORTED_MESSAGE =
  'This browser cannot play Opus audio, so this book cannot be read aloud here. Please use Chrome, Edge or Firefox.'

/** Audio that must be buffered past the playhead before play() starts. */
export const START_GATE_S = 0.3
const GATE_POLL_MS = 50

interface TierInfo {
  key: string
  factor: number
  /** This tier's times: the 1x timeline / actual_factor (or its own segments). */
  tl: Timeline
}

function initialState(): AudioState {
  return {
    isPlaying: false,
    speed: 1.0,
    availableSpeeds: [],
    currentIndex: 0,
    currentWordIndex: -1,
    voice: 'af_heart',
    buffering: false,
    elapsedSeconds: 0,
    sentenceDurations: {},
    unsupportedMessage: null,
  }
}

function detectUnsupported(): boolean {
  try {
    if (typeof Audio === 'undefined') return true
    const probe = new Audio()
    if (probe.canPlayType('audio/ogg; codecs=opus') === '') return true
  } catch {
    return true
  }
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
  return /Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\/|Firefox\/|Android/.test(ua) && /Version\//.test(ua)
}

function createAudioStore() {
  const { subscribe, set, update } = writable<AudioState>(initialState())

  let bookId: string | null = null
  let generation = 0
  let manifest: RenderManifest | null = null
  let tiers: TierInfo[] = []
  let activeKey: string | null = null
  let elements = new Map<string, HTMLAudioElement>()

  // Bumped on every tier swap and on destroy. Frames and gate waits capture it
  // and drop out when it moved, so a stale element can never write state.
  let swapGen = 0
  // Bumped on every play/pause/seek; cancels a pending start gate.
  let playToken = 0
  let rafId: number | null = null

  let primed = false
  let primePromise: Promise<void> = Promise.resolve()
  let volume = 1
  let muted = false

  // Plain mirror of the store, so the rAF loop can compare without subscribing.
  let snapshot: AudioState = initialState()
  subscribe(v => { snapshot = v })
  const cur = () => snapshot
  const activeTier = (): TierInfo | null => tiers.find(t => t.key === activeKey) ?? null
  const activeEl = (): HTMLAudioElement | null => (activeKey ? elements.get(activeKey) ?? null : null)

  function nearestTier(speed: number): TierInfo | null {
    const f = nearestSpeed(tiers.map(t => t.factor), speed)
    return tiers.find(t => t.factor === f) ?? null
  }

  function ensureElement(key: string): HTMLAudioElement {
    let el = elements.get(key)
    if (el) return el
    el = new Audio(renderAudioUrl(bookId as string, generation, key))
    el.preload = 'auto'
    el.volume = volume
    el.muted = muted
    const mine = el
    el.addEventListener('waiting', () => {
      if (elements.get(activeKey ?? '') === mine && cur().isPlaying) update(s => ({ ...s, buffering: true }))
    })
    el.addEventListener('playing', () => {
      if (elements.get(activeKey ?? '') === mine) update(s => ({ ...s, buffering: false }))
    })
    el.addEventListener('ended', () => {
      if (elements.get(activeKey ?? '') === mine) finishBook()
    })
    elements.set(key, el)
    return el
  }

  function bufferedAhead(el: HTMLAudioElement): number {
    const t = el.currentTime
    const b = el.buffered
    for (let i = 0; i < (b?.length ?? 0); i++) {
      if (b.start(i) <= t + 0.05 && b.end(i) >= t) {
        const remaining = Number.isFinite(el.duration) ? el.duration - t : Infinity
        return Math.min(b.end(i) - t, Math.max(remaining, 0) + START_GATE_S)
      }
    }
    return 0
  }

  async function waitForGate(el: HTMLAudioElement, token: number): Promise<boolean> {
    while (token === playToken) {
      if (bufferedAhead(el) >= START_GATE_S) return true
      await new Promise<void>(r => setTimeout(r, GATE_POLL_MS))
    }
    return false
  }

  function stopRaf() {
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null }
  }

  function finishBook() {
    playToken++
    stopRaf()
    activeEl()?.pause()
    update(s => ({ ...s, isPlaying: false, buffering: false, currentWordIndex: -1 }))
  }

  /** Publish sentence/word for one element reading; ignores anything not active. */
  function applyTime(el: HTMLAudioElement, gen: number) {
    if (gen !== swapGen || el !== activeEl()) return
    const tier = activeTier()
    if (!tier || tier.tl.segs.length === 0) return
    const tl = tier.tl
    const ms = el.currentTime * 1000
    const lastEnd = segEndMs(tl, tl.segs.length - 1)
    if (ms >= lastEnd && cur().isPlaying && (el.ended || ms >= lastEnd + 50)) {
      finishBook()
      return
    }
    const si = Math.max(0, segmentAt(tl, ms))
    const seg = tl.segs[si]
    let wi = -1
    if (manifest && manifest.timing_quality !== 'estimated' && seg.words.length > 0 && ms < segEndMs(tl, si)) {
      for (let i = seg.words.length - 1; i >= 0; i--) {
        if (wordStartMs(tl, si, i) <= ms) { wi = i; break }
      }
    }
    // Every frame lands here: publish only a change, so subscribers (the whole
    // reader page) do not re-run 60 times a second for nothing.
    const s = cur()
    if (s.currentIndex === seg.index && s.currentWordIndex === wi && Math.abs(s.elapsedSeconds - el.currentTime) < 0.1) return
    set({ ...s, currentIndex: seg.index, currentWordIndex: wi, elapsedSeconds: el.currentTime })
  }

  function startRaf() {
    stopRaf()
    const gen = swapGen
    const el = activeEl()
    if (!el) return
    function tick() {
      if (gen !== swapGen || el !== activeEl()) { rafId = null; return }
      applyTime(el!, gen)
      if (gen !== swapGen) return
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
  }

  function segmentStartMs(tier: TierInfo, index: number): number {
    const { tl } = tier
    if (tl.segs.length === 0) return 0
    const at = positionOf(tl, index)
    return segStartMs(tl, at >= 0 ? at : Math.min(Math.max(index, 0), tl.segs.length - 1))
  }

  /** Start of the word being heard, or the sentence start when it has no word times. */
  function currentPositionMs(tier: TierInfo, index: number, wordIndex: number): number {
    const { tl } = tier
    const at = positionOf(tl, index)
    if (at < 0) return 0
    if (manifest?.timing_quality !== 'estimated' && wordIndex >= 0 && tl.segs[at].words[wordIndex]) return wordStartMs(tl, at, wordIndex)
    return segStartMs(tl, at)
  }

  function prime() {
    if (primed || !activeKey || !bookId || get(unsupported)) return
    primed = true
    const el = ensureElement(activeKey)
    const wasMuted = el.muted
    el.muted = true
    primePromise = Promise.resolve()
      .then(() => el.play())
      .then(() => { el.pause() })
      .catch(() => { primed = false })
      .finally(() => { el.muted = wasMuted })
  }
  const unsupported = writable(false)

  async function startPlayback(el: HTMLAudioElement, token: number) {
    update(s => ({ ...s, isPlaying: true, buffering: true }))
    await primePromise
    if (token !== playToken) return
    if (!(await waitForGate(el, token))) return
    try {
      await el.play()
    } catch {
      if (token === playToken) update(s => ({ ...s, isPlaying: false, buffering: false }))
      return
    }
    if (token !== playToken) return
    startRaf()
  }

  function sentenceDurations(tier: TierInfo): Record<number, number> {
    return durationsOf(tier.tl)
  }

  function teardownElements() {
    for (const el of elements.values()) {
      try { el.pause() } catch {}
      try { el.removeAttribute('src'); el.load?.() } catch {}
    }
    elements = new Map()
  }

  function seekTo(index: number) {
    const tier = activeTier()
    const el = activeEl()
    if (!tier || !el) return null
    el.currentTime = segmentStartMs(tier, index) / 1000
    return el
  }

  return {
    subscribe,

    /** Opens a book. Nothing is fetched until a generation is loaded and play() is called. */
    init(bid: string) {
      this.destroy()
      bookId = bid
      speedDowngradeStore.set(null)
    },

    /** Binds the ready generation's manifest; tier elements are created lazily. */
    load(gen: number, raw: RenderManifest) {
      if (!bookId) return
      teardownElements()
      swapGen++
      playToken++
      generation = gen
      // Plain data on purpose: never wrap these arrays in reactive proxies.
      const m = normalizeManifest(raw)
      manifest = m
      tiers = Object.keys(m.tiers)
        .map(key => ({ key, factor: Number.parseFloat(key), tl: tierTimeline(m, key) }))
        .filter((t): t is TierInfo => Number.isFinite(t.factor) && t.tl !== null)
        .sort((a, b) => a.factor - b.factor)
      primed = false
      const bad = detectUnsupported()
      unsupported.set(bad)
      const speed = cur().speed
      const tier = nearestTier(speed)
      activeKey = tier?.key ?? null
      update(s => ({
        ...s,
        speed: tier?.factor ?? s.speed,
        availableSpeeds: tiers.map(t => t.factor),
        unsupportedMessage: bad ? UNSUPPORTED_MESSAGE : null,
        sentenceDurations: tier ? sentenceDurations(tier) : {},
      }))
    },

    /** Call from the first user gesture so later (async) play() calls are allowed. */
    prime,

    play(fromIndex: number) {
      if (!activeKey || get(unsupported)) return
      prime()
      const token = ++playToken
      const el = ensureElement(activeKey)
      el.pause()
      update(s => ({ ...s, currentIndex: fromIndex, currentWordIndex: -1 }))
      seekTo(fromIndex)
      void startPlayback(el, token)
    },

    pause() {
      playToken++
      stopRaf()
      activeEl()?.pause()
      update(s => ({ ...s, isPlaying: false, buffering: false, currentWordIndex: -1 }))
    },

    resume() {
      if (!activeKey || get(unsupported)) return
      prime()
      const token = ++playToken
      void startPlayback(ensureElement(activeKey), token)
    },

    seek(index: number) {
      if (!activeKey || get(unsupported)) return
      prime()
      const token = ++playToken
      const el = ensureElement(activeKey)
      // Already sounding: move the playhead and keep going. Pausing here is what
      // made a click feel dead until the start gate re-opened.
      const sounding = cur().isPlaying && !el.paused
      update(s => ({ ...s, currentIndex: index, currentWordIndex: -1 }))
      if (sounding) {
        seekTo(index)
        // If the target is not buffered the element stalls itself ('waiting'
        // shows the buffering state, 'playing' clears it); no gate needed.
        if (bufferedAhead(el) < START_GATE_S) update(s => ({ ...s, buffering: true }))
        startRaf()
        return
      }
      el.pause()
      seekTo(index)
      void startPlayback(el, token)
    },

    /**
     * Switches to the tier nearest `newSpeed` and returns the speed actually
     * selected (the request itself when no render is loaded yet).
     */
    setSpeed(newSpeed: number): number {
      const state = cur()
      const next = nearestTier(newSpeed)
      const chosen = next?.factor ?? newSpeed
      update(s => ({ ...s, speed: chosen }))
      const oldTier = activeTier()
      if (!next || !oldTier || next.key === activeKey) return chosen

      const oldEl = activeEl()
      const wasPlaying = state.isPlaying
      swapGen++
      stopRaf()
      const token = ++playToken
      if (oldEl) {
        volume = oldEl.volume
        muted = oldEl.muted
        oldEl.pause()
      }
      activeKey = next.key
      const posMs = currentPositionMs(next, state.currentIndex, state.currentWordIndex)
      if (!bookId) return chosen
      const el = ensureElement(next.key)
      el.volume = volume
      el.muted = muted
      el.currentTime = posMs / 1000
      update(s => ({ ...s, sentenceDurations: sentenceDurations(next), elapsedSeconds: posMs / 1000 }))
      if (wasPlaying) void startPlayback(el, token)
      return chosen
    },

    setCurrentIndex(idx: number) {
      update(s => ({ ...s, currentIndex: idx }))
    },

    setVoice(voice: string) {
      update(s => ({ ...s, voice }))
    },

    setVolume(v: number) {
      volume = v
      const el = activeEl()
      if (el) el.volume = v
    },

    setMuted(m: boolean) {
      muted = m
      const el = activeEl()
      if (el) el.muted = m
    },

    destroy() {
      swapGen++
      playToken++
      stopRaf()
      teardownElements()
      bookId = null
      manifest = null
      tiers = []
      activeKey = null
      primed = false
      primePromise = Promise.resolve()
      unsupported.set(false)
      speedDowngradeStore.set(null)
      set(initialState())
    },
  }
}

export const audioStore = createAudioStore()
