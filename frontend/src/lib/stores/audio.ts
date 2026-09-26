import { writable, get } from 'svelte/store'
import { TTSSocket, type WordTimestamp } from '$lib/api'

export interface AudioState {
  isPlaying: boolean
  speed: number
  currentIndex: number
  currentWordIndex: number
  voice: string
  buffering: boolean
  /**
   * Real seconds of audio already played at the current position: the summed
   * backend-reported duration of every earlier sentence plus the elapsed part of
   * the sentence playing now. Derived from real clocks only — never estimated.
   */
  elapsedSeconds: number
  /**
   * Real per-sentence durations in seconds, keyed by sentence index, exactly as
   * the backend reported them in `sentence_end.duration_ms`. Sentences the
   * backend has not synthesised yet are absent rather than guessed at.
   */
  sentenceDurations: Record<number, number>
}

/**
 * A rate the engine refused, and the one it used instead — both as the backend
 * reported them in its `speed_unavailable` message.
 */
export interface SpeedDowngrade {
  requested: number
  effective: number
}

/**
 * The engine's own answer when it cannot honour a requested playback rate.
 *
 * `null` means "the engine has not refused anything on this connection". It is
 * a plain store beside the playback state because the MediaBar has to override
 * its `speed` prop with it: the reader route passes the reader store's speed and
 * cannot be asked to thread this through, but the highlighted rate must not be
 * a rate the reader is not getting.
 */
export const speedDowngradeStore = writable<SpeedDowngrade | null>(null)

function createAudioStore() {
  const { subscribe, set, update } = writable<AudioState>({
    isPlaying: false,
    speed: 1.0,
    currentIndex: 0,
    currentWordIndex: -1,
    voice: 'af_heart',
    buffering: false,
    elapsedSeconds: 0,
    sentenceDurations: {},
  })

  let ctx: AudioContext | null = null
  let socket: TTSSocket | null = null
  let cancelled = false

  // Generation counter — bumped on every stopAll(). Captured by scheduleChunk()
  // closures so stale decode tails and onended callbacks from stopped sources
  // become no-ops instead of advancing currentIndex (which would clobber a
  // seek-backward target).
  let generation = 0

  // Session counter — bumped on every play/seek/resume/setSpeed. Sent with the
  // action to the backend, which echoes it in every sentence_start/sentence_end/
  // complete message. Also gates audio chunks via activeSessionId (see below).
  // This is how we reject messages that are still draining from a previously
  // cancelled session — neither the frontend reset nor backend cancellation can
  // flush the WebSocket RX buffer, so we must filter by tag.
  let sessionId = 0
  // Set to the current sessionId when a sentence_start with a matching sessionId
  // arrives — audio chunks (binary, untagged) are only accepted while this
  // equals sessionId. Reset to -1 in stopAll() so we always require a fresh
  // matching sentence_start before accepting chunks in a new session.
  let activeSessionId = -1

  // Serialized decode queue — each chunk waits for the previous to finish
  // scheduling so nextStartTime updates in arrival order (no marble effect).
  let decodeChain: Promise<void> = Promise.resolve()
  let nextStartTime = 0

  let activeNodes: { node: AudioBufferSourceNode; startAt: number; duration: number }[] = []

  // Sentence timing: sentence index → AudioContext time when it starts playing.
  // rAF polls this to advance currentIndex in audio-time, not receive-time.
  let sentenceTimings: Map<number, number> = new Map()
  let receivingSentenceIndex = -1
  let wordTimings: Map<number, WordTimestamp[]> = new Map()

  // Real per-sentence durations reported by the backend. These describe the book
  // at the current speed, not the playback session, so they survive pause and
  // seek — but they are discarded when the speed changes, because Kokoro's
  // native `speed` parameter changes the rendered duration of every sentence.
  let sentenceDurations: Map<number, number> = new Map()
  let elapsedSeconds = 0

  const SPEED_CHANGE_DEBOUNCE_MS = 200
  const SENTENCE_TIMING_OFFSET_S = 0.016

  let lastScheduledIndex = -1
  let speedChangeTimer: ReturnType<typeof setTimeout> | null = null
  let prefetchSpeedTimer: ReturnType<typeof setTimeout> | null = null
  let pendingSpeed = 0

  let rafId: number | null = null

  function getCtx(): AudioContext {
    if (!ctx || ctx.state === 'closed') ctx = new AudioContext()
    return ctx
  }

  function snapshotDurations(): Record<number, number> {
    const out: Record<number, number> = {}
    for (const [index, seconds] of sentenceDurations) out[index] = seconds
    return out
  }

  /**
   * Real elapsed audio at `index`: the summed backend-reported duration of every
   * sentence before it, plus the part of `index` that has actually been played
   * according to the AudioContext clock. Sentences whose duration has not been
   * reported yet contribute 0 — unknown, never estimated.
   */
  function computeElapsedSeconds(index: number): number {
    let elapsed = 0
    for (const [i, duration] of sentenceDurations) {
      if (i < index) elapsed += duration
    }
    const ac = ctx
    const startedAt = sentenceTimings.get(index)
    const duration = sentenceDurations.get(index)
    if (ac && startedAt !== undefined && duration !== undefined) {
      elapsed += Math.min(Math.max(ac.currentTime - startedAt, 0), duration)
    }
    return elapsed
  }

  /**
   * Publish elapsed audio for the sentence now playing. Called from the rAF tick,
   * so a paused player stops advancing and a seek recomputes from the new
   * position instead of interpolating from a stale value.
   */
  function publishElapsedSeconds(index: number): void {
    const next = computeElapsedSeconds(index)
    if (Math.abs(next - elapsedSeconds) < 0.1) return
    elapsedSeconds = next
    update(s => ({ ...s, elapsedSeconds }))
  }

  function startRaf() {
    if (rafId !== null) return
    function tick() {
      const ac = ctx
      if (!ac || cancelled) { rafId = null; return }
      const s = get({ subscribe })
      if (!s.isPlaying) { rafId = requestAnimationFrame(tick); return }
      const now = ac.currentTime

      // Find the latest sentence whose scheduled start is <= now.
      // Taking the max (not the min) lets us catch up if several very short
      // sentences elapsed within a single rAF frame — the alternative would
      // show an even more visibly out-of-date highlight.
      let latestReady = -1
      for (const [idx, startTime] of sentenceTimings) {
        if (startTime <= now && idx > latestReady) latestReady = idx
      }
      
      let newWordIndex = -1
      if (latestReady >= 0) {
        const sentenceStart = sentenceTimings.get(latestReady)
        const words = wordTimings.get(latestReady)
        if (sentenceStart !== undefined && words && words.length > 0) {
          const elapsed = now - sentenceStart
          // Find as last word where start <= elapsed (word spans from start to end)
          for (let i = words.length - 1; i >= 0; i--) {
            if (elapsed >= words[i].start) {
              newWordIndex = i
              break
            }
          }
        }
        // Clean up old sentenceTimings (keep current and future only)
        for (const [idx] of sentenceTimings) {
          if (idx < latestReady) sentenceTimings.delete(idx)
        }
        for (const [idx] of wordTimings) {
          if (idx < latestReady) wordTimings.delete(idx)
        }
      }
      
      if (latestReady >= 0) {
        update(s => {
          let next = s
          if (latestReady >= s.currentIndex) {
            next = { ...next, currentIndex: latestReady }
          }
          if (next.currentWordIndex !== newWordIndex) {
            next = { ...next, currentWordIndex: newWordIndex }
          }
          return next
        })
      }

      publishElapsedSeconds(latestReady >= 0 ? latestReady : s.currentIndex)

      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
  }

  function stopRaf() {
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null }
  }

  function scheduleChunk(bytes: ArrayBuffer, idx: number) {
    // Capture generation at schedule time. Both post-decode guard and
    // onended callback check that generation hasn't advanced since -- without
    // this, a seek-backward would see onended callbacks from the STOPPED nodes
    // of the previous session fire and re-advance currentIndex forward past
    // the seek target (onended runs even when source is stopped early).
    const myGen = generation
    decodeChain = decodeChain.then(async () => {
      if (cancelled || myGen !== generation) return
      const ac = getCtx()
      let buffer: AudioBuffer
      try {
        buffer = await ac.decodeAudioData(bytes.slice(0))
      } catch {
        return // skip corrupt/partial chunks
      }
      if (cancelled || myGen !== generation) return

      const source = ac.createBufferSource()
      source.buffer = buffer
      source.playbackRate.value = 1.0 // backend handles speed via Kokoro native param
      source.connect(ac.destination)

      if (nextStartTime > 0 && nextStartTime < ac.currentTime - 0.5) nextStartTime = ac.currentTime
      const startAt = Math.max(ac.currentTime, nextStartTime)

      if (idx >= 0 && !sentenceTimings.has(idx)) {
        sentenceTimings.set(idx, startAt + SENTENCE_TIMING_OFFSET_S)
      }

      if (idx >= 0) lastScheduledIndex = idx
      source.start(startAt)
      const entry = { node: source, startAt, duration: buffer.duration }
      activeNodes.push(entry)
      source.onended = () => {
        activeNodes = activeNodes.filter(e => e !== entry)
        // If we've stopped and restarted since this node was created, do NOT
        // touch currentIndex — the new session owns that state now.
        if (myGen !== generation) return
        // Fallback: advance highlight if rAF missed this sentence's window
        // (very short sentences that completed between two rAF ticks).
        update(s => s.currentIndex <= idx ? { ...s, currentIndex: idx } : s)
      }

      nextStartTime = startAt + buffer.duration
      const ahead = nextStartTime - ac.currentTime
      update(s => ({ ...s, buffering: ahead < 0.3 }))

      if (ac.state === 'suspended') ac.resume().catch(() => {})
    })
  }

  function applyPendingSpeedChange() {
    speedChangeTimer = null
    const speed = pendingSpeed  // save before resetForPlay() clears it via stopAll()
    const bestIdx = lastScheduledIndex >= 0
      ? Math.max(lastScheduledIndex, get({ subscribe }).currentIndex)
      : get({ subscribe }).currentIndex
    resetForPlay()
    update(s => ({ ...s, isPlaying: true, buffering: true, currentIndex: bestIdx }))
    socket!.play(bestIdx, get({ subscribe }).voice, speed, sessionId)
  }

  function stopAll() {
    generation++
    cancelled = true
    if (speedChangeTimer) { clearTimeout(speedChangeTimer); speedChangeTimer = null }
    if (prefetchSpeedTimer) { clearTimeout(prefetchSpeedTimer); prefetchSpeedTimer = null }
    pendingSpeed = 0
    stopRaf()
    for (const { node } of activeNodes) {
      try { node.stop(0) } catch {}
    }
    activeNodes = []
    lastScheduledIndex = -1
    sentenceTimings.clear()
    wordTimings.clear()
    nextStartTime = 0
    receivingSentenceIndex = -1
    activeSessionId = -1
    decodeChain = Promise.resolve()
  }

  function resetForPlay() {
    stopAll()
    cancelled = false
    update(s => ({ ...s, currentWordIndex: -1 }))
    sessionId++
    const ac = getCtx()
    try { if (ac.state === 'suspended') void ac.resume() } catch (e) { console.warn('AudioContext resume failed:', e) }
    startRaf()
  }

  return {
    subscribe,

    init(bid: string) {
      // Durations describe a book at a speed, so a new book starts from nothing.
      sentenceDurations = new Map()
      elapsedSeconds = 0
      update(s => ({ ...s, elapsedSeconds: 0, sentenceDurations: {} }))
      // A new connection means a new engine session: whatever rate the previous
      // one refused says nothing about this one.
      speedDowngradeStore.set(null)

      socket = new TTSSocket(bid)

      socket.onAudioChunk = (bytes: ArrayBuffer) => {
        // Binary chunks can't carry a session_id tag themselves — we gate them
        // via activeSessionId, which only matches after a sentence_start from
        // the current session has armed us.
        if (activeSessionId !== sessionId) return
        scheduleChunk(bytes, receivingSentenceIndex)
      }

      socket.onSentenceStart = (index: number, sid: number) => {
        // A stale sentence_start from a cancelled session would otherwise arm
        // activeSessionId and let stale chunks through — drop it here.
        if (sid !== sessionId) return
        activeSessionId = sid
        receivingSentenceIndex = index
      }

      socket.onSentenceEnd = (index: number, durationMs: number, sid: number, wordTimestamps?: WordTimestamp[]) => {
        if (sid !== sessionId) return
        // Real rendered length of this sentence at the current speed. This is the
        // only source of duration in the app — nothing extrapolates from it.
        if (Number.isFinite(durationMs) && durationMs > 0) {
          sentenceDurations.set(index, durationMs / 1000)
          update(s => ({ ...s, sentenceDurations: snapshotDurations() }))
        }
        if (wordTimestamps) {
          wordTimings.set(index, wordTimestamps)
        }
      }

      socket.onComplete = (sid: number) => {
        if (sid !== sessionId) return
        update(s => ({ ...s, isPlaying: false, buffering: false }))
        stopRaf()
      }

      // The engine cannot render at the requested rate and is producing
      // `effectiveSpeed` instead. Playing 1.0x audio under a 1.5x highlight is
      // the UI telling the reader something untrue, so the store takes the
      // engine's word for it: the speed becomes the effective rate, and the
      // refusal is published for the transport controls to explain.
      socket.onSpeedUnavailable = (requested: number, effective: number, sid: number) => {
        if (sid !== sessionId) return
        // A queued speed change would re-request the rate the engine just
        // refused, and would overwrite the effective speed below.
        if (speedChangeTimer) { clearTimeout(speedChangeTimer); speedChangeTimer = null }
        pendingSpeed = 0
        const changed = effective !== get({ subscribe }).speed
        update(s => ({ ...s, speed: effective }))
        // Every duration measured so far describes the rate this engine was
        // assumed to be rendering at; none of them describe `effective`.
        if (changed) {
          sentenceDurations = new Map()
          elapsedSeconds = 0
          update(s => ({ ...s, elapsedSeconds: 0, sentenceDurations: {} }))
        }
        speedDowngradeStore.set({ requested, effective })
      }

      socket.connect()
    },

    play(fromIndex: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, currentWordIndex: -1, isPlaying: true, buffering: true, currentIndex: fromIndex }))
      const state = get({ subscribe })
      socket.play(fromIndex, state.voice, state.speed, sessionId)
    },

    pause() {
      if (!socket) return
      socket.pause()
      stopAll()
      // Clear buffering too — otherwise the spinner can stay up forever if we
      // paused mid-buffer.
      update(s => ({ ...s, isPlaying: false, buffering: false, currentWordIndex: -1 }))
    },

    resume() {
      const state = get({ subscribe })
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, currentWordIndex: -1, isPlaying: true, buffering: true }))
      socket.play(state.currentIndex, state.voice, state.speed, sessionId)
    },

    seek(index: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, currentWordIndex: -1, isPlaying: true, currentIndex: index, buffering: true }))
      const state = get({ subscribe })
      socket.seek(index, state.voice, state.speed, sessionId)
    },

    setSpeed(newSpeed: number) {
      // Once the engine has said it cannot render anything but `effective`, any
      // other request is accepted and then silently ignored — exactly the lie
      // the notice exists to prevent. The transport's buttons are disabled, but
      // the reader's ↑/↓ hotkeys reach this same method, so the refusal is
      // enforced here too. A new connection (init) clears the refusal and asks
      // the engine again.
      if (get(speedDowngradeStore)) return
      const state = get({ subscribe })
      update(s => ({ ...s, speed: newSpeed }))
      // Kokoro renders speed natively, so every measured duration describes the
      // previous speed only. Drop them rather than reuse numbers that no longer
      // describe what will be played.
      if (newSpeed !== state.speed) {
        sentenceDurations = new Map()
        elapsedSeconds = 0
        update(s => ({ ...s, elapsedSeconds: 0, sentenceDurations: {} }))
      }
      if (!socket) return
      // Debounced cache warm-up at new speed (100ms) — prevents rapid task
      // cancellations when user drags a speed slider quickly.
      if (prefetchSpeedTimer) clearTimeout(prefetchSpeedTimer)
      prefetchSpeedTimer = setTimeout(() => {
        prefetchSpeedTimer = null
        if (!socket) return
        const warmIdx = lastScheduledIndex >= 0
          ? Math.max(lastScheduledIndex, get({ subscribe }).currentIndex)
          : get({ subscribe }).currentIndex
        socket.prefetchSpeed(warmIdx, get({ subscribe }).voice, newSpeed)
      }, 100)
      if (!state.isPlaying) return
      pendingSpeed = newSpeed
      update(s => ({ ...s, buffering: true }))
      if (speedChangeTimer) clearTimeout(speedChangeTimer)
      speedChangeTimer = setTimeout(applyPendingSpeedChange, SPEED_CHANGE_DEBOUNCE_MS)
    },

    setCurrentIndex(idx: number) {
      update(s => ({ ...s, currentIndex: idx }))
    },

    setVoice(voice: string) {
      update(s => ({ ...s, voice }))
    },

    destroy() {
      stopAll()
      socket?.disconnect()
      socket = null
      ctx?.close()
      ctx = null
      sentenceDurations = new Map()
      elapsedSeconds = 0
      speedDowngradeStore.set(null)
      set({
        isPlaying: false,
        speed: 1.0,
        currentIndex: 0,
        currentWordIndex: -1,
        voice: 'af_heart',
        buffering: false,
        elapsedSeconds: 0,
        sentenceDurations: {},
      })
    },
  }
}

export const audioStore = createAudioStore()
