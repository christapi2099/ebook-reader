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

  // Serialized decode queue — each chunk waits for the previous to finish scheduling
  // so nextStartTime is always updated in arrival order, preventing marble effect.
  let decodeChain: Promise<void> = Promise.resolve()
  let nextStartTime = 0

  // Active nodes keyed by their scheduled start time for speed-change updates
  let activeNodes: { node: AudioBufferSourceNode; startAt: number; duration: number }[] = []

  // Sentence timing: maps sentence index → AudioContext time when it will start playing.
  // Updated as chunks arrive and get scheduled. Used by the rAF loop to sync highlight.
  let sentenceTimings: Map<number, number> = new Map()
  let receivingSentenceIndex = -1   // sentence whose chunks are currently arriving
  let sentenceStartRecorded = false // whether we've recorded the timing for current sentence

  let rafId: number | null = null

  function getCtx(): AudioContext {
    if (!ctx || ctx.state === 'closed') ctx = new AudioContext()
    return ctx
  }

  // rAF loop: advances currentIndex when AudioContext time crosses each sentence's
  // scheduled start time, so the highlight moves in audio time not receive time.
  function startRaf() {
    if (rafId !== null) return
    function tick() {
      const ac = ctx
      if (!ac || cancelled) { rafId = null; return }
      const now = ac.currentTime

      // Find the latest sentence whose scheduled start is <= now
      let latestReady = -1
      for (const [idx, startTime] of sentenceTimings) {
        if (startTime <= now && idx > latestReady) latestReady = idx
      }
      if (latestReady >= 0) {
        update(s => {
          if (latestReady > s.currentIndex) return { ...s, currentIndex: latestReady }
          return s
        })
        // Prune timings we've passed
        for (const [idx] of sentenceTimings) {
          if (idx < latestReady) sentenceTimings.delete(idx)
        }
      }

      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
  }

  function stopRaf() {
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null }
  }

  function scheduleChunk(bytes: ArrayBuffer) {
    // Chain decode+schedule so they execute strictly in order
    decodeChain = decodeChain.then(async () => {
      if (cancelled) return
      const ac = getCtx()
      let buffer: AudioBuffer
      try {
        buffer = await ac.decodeAudioData(bytes.slice(0))
      } catch {
        return // skip corrupt/partial chunks
      }
      if (cancelled) return

      const speed = get({ subscribe }).speed
      const source = ac.createBufferSource()
      source.buffer = buffer
      source.playbackRate.value = speed
      source.connect(ac.destination)

      const startAt = Math.max(nextStartTime, ac.currentTime + 0.04)

      // Record sentence timing on the FIRST chunk of each sentence
      if (!sentenceStartRecorded && receivingSentenceIndex >= 0) {
        sentenceTimings.set(receivingSentenceIndex, startAt)
        sentenceStartRecorded = true
      }

      source.start(startAt)
      const entry = { node: source, startAt, duration: buffer.duration }
      activeNodes.push(entry)
      source.onended = () => { activeNodes = activeNodes.filter(e => e !== entry) }

      nextStartTime = startAt + buffer.duration / speed

      if (ac.state === 'suspended') ac.resume()
    })
  }

  function stopAll() {
    cancelled = true
    stopRaf()
    for (const { node } of activeNodes) {
      try { node.stop(0) } catch {}
    }
    activeNodes = []
    sentenceTimings.clear()
    nextStartTime = 0
    receivingSentenceIndex = -1
    sentenceStartRecorded = false
    // Drain the chain by replacing it
    decodeChain = Promise.resolve()
  }

  function resetForPlay() {
    stopAll()
    cancelled = false
    const ac = getCtx()
    if (ac.state === 'suspended') ac.resume()
    startRaf()
  }

  return {
    subscribe,

    init(bid: string) {
      socket = new TTSSocket(bid)

      socket.onAudioChunk = (bytes: ArrayBuffer) => {
        update(s => ({ ...s, buffering: false }))
        scheduleChunk(bytes)
      }

      socket.onSentenceStart = (index: number) => {
        // Mark which sentence is now sending chunks; don't update currentIndex here —
        // the rAF loop will do that when the audio actually reaches playback time.
        receivingSentenceIndex = index
        sentenceStartRecorded = false
      }

      socket.onSentenceEnd = (_index: number, _durationMs: number) => {}

      socket.onComplete = () => {
        update(s => ({ ...s, isPlaying: false, buffering: false }))
        stopRaf()
      }

      socket.connect()
    },

    play(fromIndex: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, isPlaying: true, buffering: true, currentIndex: fromIndex }))
      socket.play(fromIndex, get({ subscribe }).voice)
    },

    pause() {
      if (!socket) return
      socket.pause()
      stopAll()
      update(s => ({ ...s, isPlaying: false }))
    },

    resume() {
      const state = get({ subscribe })
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, isPlaying: true, buffering: true }))
      socket.play(state.currentIndex, state.voice)
    },

    seek(index: number) {
      if (!socket) return
      resetForPlay()
      update(s => ({ ...s, currentIndex: index, buffering: true }))
      socket.seek(index)
    },

    setSpeed(newSpeed: number) {
      update(s => ({ ...s, speed: newSpeed }))
      const ac = ctx
      if (!ac) return
      const now = ac.currentTime
      // Reschedule all queued nodes that haven't started yet
      for (const entry of activeNodes) {
        entry.node.playbackRate.value = newSpeed
        // Recalculate nextStartTime from the first unstarted node
        if (entry.startAt > now) {
          // Adjust sentence timing proportionally — approximate
        }
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
