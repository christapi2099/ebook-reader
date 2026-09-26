import { writable, get, type Writable } from 'svelte/store'
import {
  getSentences,
  getProgress,
  saveProgress,
  saveProgressBeacon,
  type Sentence,
} from '$lib/api'
import { userStore } from '$lib/stores/user'

export type { Sentence }

export interface ReaderState {
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

// ---------------------------------------------------------------------------
// The reading position, and who owns which half of it
//
//   * `audioStore.currentIndex` answers "where playback actually is". It is
//     advanced from the AudioContext clock and from `onended`, so it is the only
//     value that follows what the reader is hearing.
//   * `readerStore.currentIndex` answers "where the user asked to be". The page
//     indicator, the bookmark target, the rewind/forward hotkeys and the saved
//     progress all read it.
//
// Both questions are real, so both values are real and neither store is
// duplicated. `mirrorPlaybackIndex` is the only bridge between them.
//
// Before that bridge existed, only `seek()` ever wrote this store, so during
// continuous playback the saved position and the page indicator fell behind the
// audio by one sentence per sentence: a listener who played for ten minutes and
// then reloaded resumed at the last sentence they had *clicked*, or at 0.
// ---------------------------------------------------------------------------

/**
 * Minimum gap between two position writes while playback advances. A write per
 * sentence would POST every few seconds for the whole length of a book; never
 * writing between clicks is the defect this file was changed to fix.
 */
const PROGRESS_SAVE_INTERVAL_MS = 5000

let saveTimer: ReturnType<typeof setTimeout> | null = null
/** The index the server already has, so an unchanged position is never re-sent. */
let lastSavedIndex: number | null = null
let lastSavedAt = 0

function clearSaveTimer(): void {
  if (saveTimer !== null) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
}

/**
 * The one function that writes the position.
 *
 * Kept single so the seek path, the playback path and the unload path cannot
 * drift apart in what they persist, and so `lastSavedIndex` can never disagree
 * with what the server was actually told.
 */
async function writeProgress(bookId: string, index: number): Promise<void> {
  lastSavedIndex = index
  lastSavedAt = Date.now()
  await saveProgress(bookId, index).catch(() => {})
  // The home page's "continue reading" reads this, not Progress, so it is the
  // same position rather than a second concern.
  userStore.updateLastRead(bookId, index).catch(() => {})
}

/** Write the current position, unless the server already has it. */
function writeCurrentPosition(): Promise<void> {
  const { bookId, currentIndex } = get(readerStore)
  if (bookId === null || currentIndex === lastSavedIndex) return Promise.resolve()
  return writeProgress(bookId, currentIndex)
}

/**
 * Queue a write, coalescing bursts to at most one per `PROGRESS_SAVE_INTERVAL_MS`.
 *
 * The first change after a quiet period writes straight away, so the position
 * becomes durable as soon as it moves; changes arriving inside the window are
 * folded into a single later write. One timer, so writes cannot pile up behind
 * each other.
 */
function scheduleProgressSave(): void {
  clearSaveTimer()
  const wait = PROGRESS_SAVE_INTERVAL_MS - (Date.now() - lastSavedAt)
  if (wait <= 0) {
    void writeCurrentPosition()
    return
  }
  saveTimer = setTimeout(() => {
    saveTimer = null
    void writeCurrentPosition()
  }, wait)
}

/**
 * Save the position now, for the moments a throttle would lose it outright:
 * pausing, leaving the reader, and the page unloading.
 *
 * `beacon` sends through `navigator.sendBeacon`, the one request a browser
 * guarantees to deliver while the page is going away — an ordinary fetch is
 * cancelled with the page. Returns whether the position reached the server, so a
 * caller can tell "saved" from "the page is already gone".
 */
export function flushProgress(beacon = false): boolean {
  clearSaveTimer()
  const { bookId, currentIndex } = get(readerStore)
  if (bookId === null || currentIndex === lastSavedIndex) return true
  if (!beacon) {
    void writeProgress(bookId, currentIndex)
    return true
  }
  if (!saveProgressBeacon(bookId, currentIndex)) return false
  // `userStore.updateLastRead` is a second request and a beacon cannot carry it,
  // so the position is what gets forced out here. The last-read book is
  // refreshed by the next ordinary save.
  lastSavedIndex = currentIndex
  lastSavedAt = Date.now()
  return true
}

export const loadBook = async (bookId: string): Promise<void> => {
  const sentences: Sentence[] = await getSentences(bookId)

  let currentIndex = 0
  try {
    currentIndex = await getProgress(bookId)
  } catch {
    // No progress saved yet — start from 0
  }
  // This position came *from* the server, so it is nothing to write back.
  lastSavedIndex = currentIndex
  lastSavedAt = Date.now()

  readerStore.update(s => ({ ...s, bookId, sentences, currentIndex }))

  // Update user settings with the last read book (silently ignore errors)
  userStore.updateLastRead(bookId, currentIndex).catch(() => {})
}

export const seek = async (index: number): Promise<void> => {
  const { bookId, sentences } = get(readerStore)
  const clamped = Math.max(0, Math.min(index, sentences.length - 1))
  readerStore.update(s => ({ ...s, currentIndex: clamped }))
  if (bookId === null) return
  // Persist the clamped value, not the requested one. `seek(999)` on a
  // hundred-sentence book used to store 999 — the server does not clamp this
  // field — and the reader then resumed out of bounds.
  clearSaveTimer()
  await writeProgress(bookId, clamped)
}

/**
 * Copy the audio store's playback position into this store, while playing.
 *
 * The `isPlaying` guard is load-bearing. A paused reader who seeks is the
 * authority on where they are, and a late advance from a cancelled session must
 * not drag them forward — `audio.ts` needed a generation counter for exactly
 * that class of stale event, and this must not reintroduce it.
 */
export const mirrorPlaybackIndex = (index: number, isPlaying: boolean): void => {
  if (!isPlaying) return
  let moved = false
  readerStore.update(s => {
    if (s.currentIndex === index) return s
    moved = true
    return { ...s, currentIndex: index }
  })
  if (moved) scheduleProgressSave()
}

export const setSpeed = (speed: number): void => {
  readerStore.update(s => ({ ...s, speed: Math.max(0.5, Math.min(speed, 3.0)) }))
}

export const setPlaying = (val: boolean): void => {
  readerStore.update(s => ({ ...s, isPlaying: val }))
}

/**
 * The next (or previous) sentence that is not filtered out, or `null` at the
 * end of the book. One walk, shared, so the two directions cannot disagree.
 */
function stepSentence(delta: 1 | -1): number | null {
  const { currentIndex, sentences } = get(readerStore)
  let next = currentIndex + delta
  while (next >= 0 && next < sentences.length && sentences[next].filtered) next += delta
  if (next < 0 || next >= sentences.length) return null
  return next
}

// Both of these go through `seek` rather than writing `currentIndex` directly.
// They used to move the position without persisting it or telling the audio
// store, which is precisely the disagreement between the two positions that the
// persistence above exists to remove.
export const nextSentence = async (): Promise<void> => {
  const next = stepSentence(1)
  if (next !== null) await seek(next)
}

export const prevSentence = async (): Promise<void> => {
  const prev = stepSentence(-1)
  if (prev !== null) await seek(prev)
}

export default readerStore
