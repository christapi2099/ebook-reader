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

  let audio: HTMLAudioElement | null = null
  let socket: TTSSocket | null = null
  let bookId: string | null = null
  let chunkQueue: ArrayBuffer[] = []
  let mediaSource: MediaSource | null = null
  let sourceBuffer: SourceBuffer | null = null
  let appendPending = false

  function initAudio() {
    if (audio) return
    audio = new Audio()
    // @ts-ignore — preservesPitch is supported in all modern browsers
    audio.preservesPitch = true
    audio.addEventListener('ended', () => {
      // Feed next queued chunk
      if (chunkQueue.length > 0) appendNextChunk()
    })
  }

  function appendNextChunk() {
    if (!sourceBuffer || appendPending || chunkQueue.length === 0) return
    if (sourceBuffer.updating) return
    appendPending = true
    const chunk = chunkQueue.shift()!
    sourceBuffer.appendBuffer(chunk)
  }

  function initMediaSource() {
    mediaSource = new MediaSource()
    if (!audio) return
    audio.src = URL.createObjectURL(mediaSource)
    mediaSource.addEventListener('sourceopen', () => {
      if (!mediaSource) return
      sourceBuffer = mediaSource.addSourceBuffer('audio/wav')
      sourceBuffer.addEventListener('updateend', () => {
        appendPending = false
        if (chunkQueue.length > 0) appendNextChunk()
      })
    })
  }

  function resetStream() {
    chunkQueue = []
    appendPending = false
    sourceBuffer = null
    if (mediaSource) {
      try { mediaSource.endOfStream() } catch {}
    }
    mediaSource = null
    initAudio()
    initMediaSource()
  }

  return {
    subscribe,

    init(bid: string) {
      bookId = bid
      initAudio()
      socket = new TTSSocket(bid)

      socket.onAudioChunk = (bytes: ArrayBuffer) => {
        chunkQueue.push(bytes)
        update(s => ({ ...s, buffering: false }))
        appendNextChunk()
        if (audio && audio.paused && get({ subscribe }).isPlaying) {
          audio.play().catch(() => {})
        }
      }

      socket.onSentenceStart = (index: number) => {
        update(s => ({ ...s, currentIndex: index }))
      }

      socket.onSentenceEnd = (_index: number, _durationMs: number) => {}

      socket.onComplete = () => {
        update(s => ({ ...s, isPlaying: false, buffering: false }))
        if (mediaSource && mediaSource.readyState === 'open') {
          try { mediaSource.endOfStream() } catch {}
        }
      }

      socket.connect()
    },

    play(fromIndex: number) {
      if (!socket) return
      update(s => ({ ...s, isPlaying: true, buffering: true, currentIndex: fromIndex }))
      resetStream()
      socket.play(fromIndex, get({ subscribe }).voice)
      audio?.play().catch(() => {})
    },

    pause() {
      if (!socket) return
      socket.pause()
      audio?.pause()
      update(s => ({ ...s, isPlaying: false }))
    },

    resume() {
      const state = get({ subscribe })
      if (!socket) return
      update(s => ({ ...s, isPlaying: true, buffering: true }))
      resetStream()
      socket.play(state.currentIndex, state.voice)
      audio?.play().catch(() => {})
    },

    seek(index: number) {
      if (!socket) return
      update(s => ({ ...s, currentIndex: index, buffering: true }))
      resetStream()
      socket.seek(index)
      audio?.play().catch(() => {})
    },

    setSpeed(speed: number) {
      update(s => ({ ...s, speed }))
      if (audio) audio.playbackRate = speed
    },

    setVoice(voice: string) {
      update(s => ({ ...s, voice }))
    },

    destroy() {
      socket?.disconnect()
      socket = null
      audio?.pause()
      audio = null
      chunkQueue = []
      mediaSource = null
      sourceBuffer = null
      bookId = null
      set({ isPlaying: false, speed: 1.0, currentIndex: 0, voice: 'af_heart', buffering: false })
    },
  }
}

export const audioStore = createAudioStore()
