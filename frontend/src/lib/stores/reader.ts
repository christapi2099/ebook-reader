import { writable } from 'svelte/store'
import type { Writable } from 'svelte/store'
import { getSentences, getProgress, saveProgress } from '$lib/api'

export interface Sentence {
  index: number
  text: string
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
  filtered: boolean
}

export interface Book {
  id: string
  title: string
  author: string | null
  file_type: string
  page_count: number
}

interface ReaderState {
  bookId: string | null
  sentences: Sentence[]
  currentIndex: number
  isPlaying: boolean
  speed: number
}

const readerStore: Writable<ReaderState> = writable({
  bookId: null,
  sentences: [],
  currentIndex: 0,
  isPlaying: false,
  speed: 1.0,
})

export const loadBook = async (bookId: string): Promise<void> => {
  const sentences: Sentence[] = await getSentences(bookId)

  let currentIndex = 0
  try {
    currentIndex = await getProgress(bookId)
  } catch {
    // No progress saved yet — start from 0
  }

  readerStore.update(s => ({ ...s, bookId, sentences, currentIndex }))
}

export const seek = async (index: number): Promise<void> => {
  let bookId: string | null = null
  readerStore.update(s => {
    bookId = s.bookId
    const clamped = Math.max(0, Math.min(index, s.sentences.length - 1))
    return { ...s, currentIndex: clamped }
  })
  if (bookId) {
    await saveProgress(bookId, index).catch(() => {})
  }
}

export const setSpeed = (speed: number): void => {
  readerStore.update(s => ({ ...s, speed: Math.max(0.5, Math.min(speed, 3.0)) }))
}

export const setPlaying = (val: boolean): void => {
  readerStore.update(s => ({ ...s, isPlaying: val }))
}

export const nextSentence = (): void => {
  readerStore.update(s => {
    let next = s.currentIndex + 1
    while (next < s.sentences.length && s.sentences[next].filtered) next++
    if (next >= s.sentences.length) return s
    return { ...s, currentIndex: next }
  })
}

export const prevSentence = (): void => {
  readerStore.update(s => {
    let prev = s.currentIndex - 1
    while (prev >= 0 && s.sentences[prev].filtered) prev--
    if (prev < 0) return s
    return { ...s, currentIndex: prev }
  })
}

export default readerStore
