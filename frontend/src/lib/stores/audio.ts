import { writable, get } from 'svelte/store'
import { TTSSocket } from '$lib/api'

export interface AudioState {
  isPlaying: boolean
  speed: number
  currentIndex: number
  voice: string
  buffering: boolean
}

function createAudioStore() {
  const { subscribe, set, update } = writable<AudioState>({
    isPlaying: false,
    speed: 1.0,
    currentIndex: 0,
    voice: 'af_heart',
    buffering: false,
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

  let rafId: number | null = null

  function getCtx(): AudioContext {
    if (!ctx || ctx.state === 'closed') ctx = new AudioContext()
    return ctx
  }

  function startRaf() {
    if (rafId !== null) return
    function tick() {
      const ac = ctx
      if (!ac || cancelled) { rafId = null; return }
      const s = get({ subscribe })
      if (!s.isPlaying || s.buffering) { rafId = requestAnimationFrame(tick); return }
      const now = ac.currentTime

      // Find the latest sentence whose scheduled start is <= now.
      // Taking the max (not the min) lets us catch up if several very short
      // sentences elapsed within a single rAF frame — the alternative would
      // show an even more visibly out-of-date highlight.
      let latestReady = -1
      for (const [idx, startTime] of sentenceTimings) {
        if (startTime <= now && idx > latestReady) latestReady = idx
      }
      if (latestReady >= 0) {
        update(s => {
          if (latestReady >= s.currentIndex) return { ...s, currentIndex: latestReady }
          return s
        })
        for (const [idx] of sentenceTimings) {
          if (idx <= latestReady) sentenceTimings.delete(idx)
        }
      }

      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
  }

  function stopRaf() {
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null }
  }

  function scheduleChunk(bytes: ArrayBuffer, idx: number) {
    // Capture generation at schedule time. Both the post-decode guard and the
    // onended callback check that generation hasn't advanced since — without
    // this, a seek-backward would see onended callbacks from the STOPPED nodes
    // of the previous session fire and re-advance currentIndex forward past
    // the seek target (onended runs even when the source is stopped early).
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

      if (nextStartTime > 0 && nextStartTime < ac.currentTime - 0.1) nextStartTime = 0
      // Always add the scheduling buffer so the highlight never outruns the
      // actual audio output — without it, the rAF can fire the highlight ~80-300ms
      // before the audio reaches the speakers when nextStartTime dominates.
      const startAt = Math.max(ac.currentTime, nextStartTime) + 0.08

      // Record timing on the FIRST chunk of each sentence. Use the CAPTURED idx
      // (not receivingSentenceIndex) because receivingSentenceIndex may have
      // advanced to the next sentence before this decode promise executes.
      if (idx >= 0 && !sentenceTimings.has(idx)) {
        sentenceTimings.set(idx, startAt)
      }

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

      try { if (ac.state === 'suspended') await ac.resume() } catch (e) { console.warn('AudioContext resume failed:', e) }
    })
  }

  function stopAll() {
    generation++
    cancelled = true
    stopRaf()
    for (const { node } of activeNodes) {
      try { node.stop(0) } catch {}
    }
    activeNodes = []
    sentenceTimings.clear()
    nextStartTime = 0
    receivingSentenceIndex = -1
    activeSessionId = -1 // force a fresh sentence_start before we accept any chunks
    decodeChain = Promise.resolve()
  }

  function resetForPlay() {
    stopAll()
    cancelled = false
    // New session — any in-flight messages tagged with the old sessionId will
    // be rejected by the onSentenceStart / onAudioChunk guards below.
    sessionId++
    const ac = getCtx()
    try { if (ac.state === 'suspended') void ac.resume() } catch (e) { console.warn('AudioContext resume failed:', e) }
    startRaf()
  }

  return {
    subscribe,

    init(bid: string) {
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

      socket.onSentenceEnd = (_index: number, _durationMs: number, _sid: number) => {}

      socket.onComplete = (sid: number) => {
        if (sid !== sessionId) return
        update(s => ({ ...s, isPlaying: false, buffering: false }))
        stopRaf()
      }

      socket.connect()
    },

    play(fromIndex: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, isPlaying: true, buffering: true, currentIndex: fromIndex }))
      const state = get({ subscribe })
      socket.play(fromIndex, state.voice, state.speed, sessionId)
    },

    pause() {
      if (!socket) return
      socket.pause()
      stopAll()
      // Clear buffering too — otherwise the spinner can stay up forever if we
      // paused mid-buffer.
      update(s => ({ ...s, isPlaying: false, buffering: false }))
    },

    resume() {
      const state = get({ subscribe })
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, isPlaying: true, buffering: true }))
      socket.play(state.currentIndex, state.voice, state.speed, sessionId)
    },

    seek(index: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, isPlaying: true, currentIndex: index, buffering: true }))
      const state = get({ subscribe })
      socket.seek(index, state.voice, state.speed, sessionId)
    },

    setSpeed(newSpeed: number) {
      const state = get({ subscribe })
      update(s => ({ ...s, speed: newSpeed }))
      if (state.isPlaying && socket) {
        const currentIdx = state.currentIndex
        resetForPlay()
        update(s => ({ ...s, isPlaying: true, buffering: true, currentIndex: currentIdx }))
        socket.play(currentIdx, state.voice, newSpeed, sessionId)
      }
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
      set({ isPlaying: false, speed: 1.0, currentIndex: 0, voice: 'af_heart', buffering: false })
    },
  }
}

export const audioStore = createAudioStore()
