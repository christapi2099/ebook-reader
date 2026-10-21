import { writable, get } from 'svelte/store'
import { renderAudioUrl, type RenderManifest } from '$lib/api'
import { nearestSpeed } from '$lib/utils/speed-tiers'
import { toastStore } from '$lib/stores/toast'
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
/** A tier swap while playing waits until the new tier has this much buffered past the target. */
const PREWARM_S = 2
/** Wall-clock cap on waiting for the new tier; past it the swap is abandoned, never forced. */
const SWAP_GIVE_UP_MS = 12000
/** While waiting, re-aim the new element at the moving target at most this often. */
const SWAP_CHASE_MS = 1500
/** A swap target closer than this to where the element already sits needs no new seek. */
const SWAP_SEEK_EPSILON_S = 0.25

/**
 * Set to the speed still sounding when a swap to another tier had to be
 * abandoned, so the page can put its speed buttons back.
 */
export const speedRevertStore = writable<number | null>(null)

interface TierInfo {
  key: string
  factor: number
  /** This tier's times: the 1x timeline / actual_factor (or its own segments). */
  tl: Timeline
}

const DEBUG_EVENTS = ['loadstart', 'loadedmetadata', 'durationchange', 'seeking', 'seeked', 'canplay',
  'canplaythrough', 'playing', 'pause', 'waiting', 'stalled', 'suspend', 'error', 'ended'] as const
const DEBUG_MAX = 2000

/** Off unless the reader URL has ?audioDebug=1 (or localStorage audioDebug=1). */
function debugEnabled(): boolean {
  try {
    return typeof location !== 'undefined' && (/[?&]audioDebug=1\b/.test(location.search) || localStorage.getItem('audioDebug') === '1')
  } catch {
    return false
  }
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

  // Timestamped audio events for diagnosing a real stall: `window.__audioDebug`
  // holds them, `__audioDebug.dump()` prints a table to copy from the console.
  const debugOn = debugEnabled()
  const debugLog: Record<string, unknown>[] = []
  function dbg(what: string, el?: HTMLAudioElement | null, extra: Record<string, unknown> = {}) {
    if (!debugOn) return
    const b = el?.buffered
    debugLog.push({
      t: Math.round(performance.now()), what, tier: el ? el.src.split('audio_')[1] : undefined,
      rs: el?.readyState, ns: el?.networkState, ct: el ? +el.currentTime.toFixed(2) : undefined,
      dur: el && Number.isFinite(el.duration) ? Math.round(el.duration) : el?.duration,
      bufEnd: b && b.length ? Math.round(b.end(b.length - 1)) : 0, err: el?.error?.code, ...extra,
    })
    if (debugLog.length > DEBUG_MAX) debugLog.splice(0, debugLog.length - DEBUG_MAX)
  }
  if (debugOn && typeof window !== 'undefined') {
    ;(window as unknown as Record<string, unknown>).__audioDebug = Object.assign(debugLog, {
      dump: () => console.table(debugLog),
      json: () => JSON.stringify(debugLog),
    })
    console.info('[audio] debug on: window.__audioDebug.dump() prints the event log')
  }

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
  // A tier swap while playing keeps the old tier sounding until the new one is
  // buffered at the target; this id (and the tier) identify that pending swap.
  let pendingSwap = 0
  let pendingTier: TierInfo | null = null

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
    if (debugOn) for (const ev of DEBUG_EVENTS) el.addEventListener(ev, () => dbg(ev, mine))
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
      // Firefox publishes the new buffered range ~500 ms after a seek although data is ready
      if (bufferedAhead(el) >= START_GATE_S || (el.readyState >= 3 && !el.seeking)) return true
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

  /**
   * Where the playhead of `from` sits on `to`, in ms: the same sentence at the
   * same offset, scaled by the tiers' speed ratio. Read from the element clock
   * (the only clock there is), not from the per-frame mirror, which stops in a
   * hidden tab. An element that never played falls back to the sentence start.
   */
  function mapPosition(from: TierInfo, fromEl: HTMLAudioElement | null, to: TierInfo, fallbackIndex: number): number {
    const t = fromEl?.currentTime ?? 0
    if (fromEl && t > 0 && from.tl.segs.length > 0) {
      const ms = t * 1000
      const si = segmentAt(from.tl, ms)
      const ni = si >= 0 ? positionOf(to.tl, from.tl.segs[si].index) : -1
      if (si >= 0 && ni >= 0) {
        const scale = from.tl.segs === to.tl.segs ? from.tl.div / to.tl.div : from.factor / to.factor
        return segStartMs(to.tl, ni) + (ms - segStartMs(from.tl, si)) * scale
      }
    }
    return segmentStartMs(to, fallbackIndex)
  }

  /** True when `el` has `ahead` seconds (or the rest of the file) buffered from `t`. */
  function covers(el: HTMLAudioElement, t: number, ahead: number): boolean {
    const b = el.buffered
    for (let i = 0; i < (b?.length ?? 0); i++) {
      if (b.start(i) <= t + 0.05 && b.end(i) >= t) {
        const need = Number.isFinite(el.duration) ? Math.min(ahead, Math.max(el.duration - t, 0)) : ahead
        return b.end(i) - t >= need
      }
    }
    return false
  }

  /** Makes `next` the active tier at the exact spot now playing, resuming when asked. */
  function swapTo(next: TierInfo, resume: boolean) {
    const oldTier = activeTier()
    if (!bookId || !oldTier) return
    const state = cur()
    const oldEl = activeEl()
    const posMs = mapPosition(oldTier, oldEl, next, state.currentIndex)
    swapGen++
    stopRaf()
    const token = ++playToken
    if (oldEl) {
      volume = oldEl.volume
      muted = oldEl.muted
      oldEl.pause()
    }
    activeKey = next.key
    dbg('swap-commit', oldEl, { to: next.key, posMs: Math.round(posMs), resume })
    const el = ensureElement(next.key)
    el.volume = volume
    el.muted = muted
    // A prewarmed element already sits on the target: seeking again would throw
    // its loaded data away and reread the file from there.
    if (!(Math.abs(el.currentTime - posMs / 1000) < SWAP_SEEK_EPSILON_S && el.readyState >= 1)) el.currentTime = posMs / 1000
    update(s => ({ ...s, sentenceDurations: sentenceDurations(next), elapsedSeconds: posMs / 1000 }))
    if (resume) void startPlayback(el, token)
  }

  /** Drops a swap whose tier would not load: keep the old tier, tell the user, reset the buttons. */
  function abandonSwap(next: TierInfo) {
    const back = activeTier()?.factor ?? cur().speed
    const dead = elements.get(next.key)
    dbg('swap-abandoned', dead, { to: next.key })
    if (dead) {
      try { dead.pause(); dead.removeAttribute('src'); dead.load?.() } catch {}
      elements.delete(next.key)
    }
    pendingTier = null
    update(s => ({ ...s, speed: back }))
    toastStore.push({
      tone: 'warning',
      title: `Could not switch to ${next.factor}x`,
      message: `This browser did not load that speed in time, so playback stays at ${back}x.`,
    })
    speedRevertStore.set(back)
  }

  /** Finishes a pending swap at once (pause, play and resume must act on the chosen tier). */
  function commitPendingSwap() {
    const next = pendingTier
    pendingSwap++
    pendingTier = null
    if (next && next.key !== activeKey) swapTo(next, false)
  }

  /** True when the paused element's playhead already sits inside sentence `index`. */
  function isInSentence(el: HTMLAudioElement, index: number): boolean {
    const tier = activeTier()
    if (!tier || tier.tl.segs.length === 0 || el.ended || !(el.currentTime > 0)) return false
    const ms = el.currentTime * 1000
    if (ms >= segEndMs(tier.tl, tier.tl.segs.length - 1)) return false
    const at = segmentAt(tier.tl, ms)
    return at >= 0 && tier.tl.segs[at].index === index
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
      pendingSwap++
      pendingTier = null
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

    /** `restart` forces a seek to the sentence start even when the playhead is already inside it. */
    play(fromIndex: number, restart = false) {
      dbg('play', activeEl(), { fromIndex, restart })
      commitPendingSwap()
      if (!activeKey || get(unsupported)) return
      prime()
      const token = ++playToken
      const el = ensureElement(activeKey)
      // Resuming a pause inside this very sentence continues from the exact
      // point: no seek (a cold seek is the slow step in Firefox) and no replay.
      const sameSpot = !restart && isInSentence(el, fromIndex)
      if (!sameSpot) el.pause()
      update(s => ({ ...s, currentIndex: fromIndex, currentWordIndex: sameSpot ? s.currentWordIndex : -1 }))
      if (!sameSpot) seekTo(fromIndex)
      void startPlayback(el, token)
    },

    pause() {
      dbg('pause-call', activeEl())
      commitPendingSwap()
      playToken++
      stopRaf()
      activeEl()?.pause()
      update(s => ({ ...s, isPlaying: false, buffering: false, currentWordIndex: -1 }))
    },

    resume() {
      commitPendingSwap()
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
      if (!next || !oldTier || next.key === activeKey) {
        // Back on the tier that is still sounding: drop the pending swap.
        pendingSwap++
        pendingTier = null
        return chosen
      }
      if (!bookId) return chosen
      const oldEl = activeEl()
      if (!(state.isPlaying && oldEl && !oldEl.paused)) {
        pendingSwap++
        pendingTier = null
        swapTo(next, state.isPlaying)
        return chosen
      }

      // Playing: the old tier keeps sounding while the new element loads its
      // target (a cold seek into an unbuffered Ogg file is slow, most of all in
      // Firefox). The target keeps moving while it loads, so the wait is for the
      // new tier to cover where the playhead is NOW, and the swap then lands in
      // buffered audio. If it never loads, the swap is abandoned, not forced.
      const id = ++pendingSwap
      pendingTier = next
      dbg('swap-start', oldEl, { to: next.key })
      const el = ensureElement(next.key)
      el.volume = volume
      el.muted = muted
      const targetS = () => mapPosition(oldTier, oldEl, next, cur().currentIndex) / 1000
      const started = Date.now()
      let aimedAt = started
      el.currentTime = targetS()
      void (async () => {
        while (id === pendingSwap) {
          const t = targetS()
          if (covers(el, t, PREWARM_S)) break
          if (el.readyState >= 3 && !el.seeking && Math.abs(el.currentTime - t) < SWAP_SEEK_EPSILON_S * 2) break
          if (el.error || Date.now() - started > SWAP_GIVE_UP_MS) {
            abandonSwap(next)
            return
          }
          if (Date.now() - aimedAt > SWAP_CHASE_MS && !covers(el, t, 0.3)) {
            el.currentTime = t
            aimedAt = Date.now()
          }
          await new Promise<void>(r => setTimeout(r, GATE_POLL_MS))
        }
        if (id !== pendingSwap) return
        pendingTier = null
        dbg('swap-ready', el, { waitedMs: Date.now() - started })
        swapTo(next, cur().isPlaying)
      })()
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
      pendingSwap++
      pendingTier = null
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
