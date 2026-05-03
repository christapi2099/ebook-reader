<script lang="ts">
import { onMount, onDestroy } from 'svelte'
import { page } from '$app/stores'
import { get } from 'svelte/store'
import readerStore, { loadBook, seek, setPlaying, setSpeed } from '$lib/stores/reader'
import { audioStore } from '$lib/stores/audio'
import { settingsStore, type SettingsState } from '$lib/stores/settings'
import { createBookmark } from '$lib/api'
import { registerHotkeys, unregisterHotkeys } from '$lib/utils/hotkeys'
import PDFViewer from '$lib/components/PDFViewer.svelte'
import MediaBar from '$lib/components/MediaBar.svelte'
import TopToolbar from '$lib/components/TopToolbar.svelte'
import AudioProgressBar from '$lib/components/AudioProgressBar.svelte'
import PageNavigator from '$lib/components/PageNavigator.svelte'
import SettingsOverlay from '$lib/components/SettingsOverlay.svelte'
import SearchOverlay from '$lib/components/SearchOverlay.svelte'
import BookmarkPanel from '$lib/components/BookmarkPanel.svelte'

const bookId = $page.params.id as string

let reader = $state(get(readerStore))
let audio = $state(get(audioStore))
let settings: SettingsState = $state({ ...get(settingsStore) })
let seeking = $state(false)
let currentPage = $state(0)
let pageToScroll = $state<number | null>(null)
let settingsOpen = $state(false)
let searchOpen = $state(false)
let bookmarksOpen = $state(false)

// Search state
let searchMatches = $state<number[]>([])
let currentSearchIndex = $state(-1)

let unsubReader: () => void
let unsubAudio: () => void
let unsubSettings: () => void

onMount(async () => {
  unsubReader = readerStore.subscribe((v) => (reader = v))
  unsubAudio = audioStore.subscribe((v) => (audio = v))
  unsubSettings = settingsStore.subscribe((v) => (settings = v))
  await loadBook(bookId)
  audioStore.init(bookId)
  audioStore.setSpeed(get(readerStore).speed)
  audioStore.setCurrentIndex(get(readerStore).currentIndex)

  const hotkeyMap: Record<string, () => void> = {
    ' ': handlePlayPause,
    'ArrowLeft': () => handleSeek(Math.max(0, reader.currentIndex - 1)),
    'ArrowRight': () => handleSeek(Math.min(reader.sentences.length - 1, reader.currentIndex + 1)),
    'ArrowUp': () => handleSpeedChange(Math.min(3, reader.speed + 0.25)),
    'ArrowDown': () => handleSpeedChange(Math.max(0.5, reader.speed - 0.25)),
    'b': () => handleAddBookmark(),
    'B': () => handleAddBookmark(),
    'f': () => { searchOpen = true },
    'F': () => { searchOpen = true },
    'Escape': () => { settingsOpen = false; searchOpen = false; bookmarksOpen = false },
  }
  registerHotkeys(hotkeyMap)
})

onDestroy(() => {
  unsubReader?.()
  unsubAudio?.()
  unsubSettings?.()
  audioStore.destroy()
  unregisterHotkeys()
})

function handlePageJump(page: number) {
  pageToScroll = page
  setTimeout(() => { pageToScroll = null }, 100)
}

$effect(() => {
  if (!audio.isPlaying && reader.isPlaying) setPlaying(false)
})

function handlePlayPause() {
  if (audio.isPlaying) handlePause()
  else handlePlay()
}

function handlePlay() {
  setPlaying(true)
  audioStore.play(audio.currentIndex)
}

function handlePause() {
  setPlaying(false)
  audioStore.pause()
}

async function handleSeek(index: number) {
  if (seeking) return
  seeking = true
  if (reader.isPlaying) audioStore.seek(index)
  try {
    await seek(index)
  } finally {
    seeking = false
  }
}

function handleSpeedChange(s: number) {
  setSpeed(s)
  audioStore.setSpeed(s)
}

function handleRewind() {
  handleSeek(Math.max(0, reader.currentIndex - 5))
}

function handleForward() {
  handleSeek(Math.min(reader.sentences.length - 1, reader.currentIndex + 5))
}

async function handleAddBookmark() {
  if (!reader.bookId) return
  const sentence = reader.sentences[reader.currentIndex]
  const label = sentence?.text.slice(0, 50) || `Sentence ${reader.currentIndex}`
  try {
    await createBookmark(reader.bookId, reader.currentIndex, label)
    bookmarksOpen = true
  } catch {
    // silently fail
  }
}

function handleSearchResults(matches: { index: number }[], _current: number) {
  searchMatches = matches.map(m => m.index)
  currentSearchIndex = _current >= 0 ? matches.findIndex(m => m.index === _current) : -1
}

function handleBookmarkGoTo(index: number) {
  handleSeek(index)
}
</script>

<div class="flex flex-col h-full">
  <div class="border-b border-slate-200 bg-white">
    {#if searchOpen}
      <SearchOverlay
        sentences={reader.sentences}
        onClose={() => { searchOpen = false; searchMatches = []; currentSearchIndex = -1 }}
        onResults={handleSearchResults}
      />
    {/if}
    <div class="grid grid-cols-3 items-center px-3 py-2">
      <div class="flex items-center justify-center">
        <PageNavigator currentPage={currentPage} totalPages={reader.sentences.length} onGoToPage={handlePageJump} />
      </div>
      <div class="flex justify-center">
        <MediaBar
          isPlaying={reader.isPlaying}
          speed={reader.speed}
          disabled={seeking}
          onPlay={handlePlay}
          onPause={handlePause}
          onRewind={handleRewind}
          onForward={handleForward}
          onSpeedChange={handleSpeedChange}
        />
      </div>
      <div class="flex justify-end">
        <TopToolbar
          onToggleCC={() => {}}
          onCopyText={() => {}}
          onAddBookmark={handleAddBookmark}
          onShowBookmarks={() => (bookmarksOpen = true)}
          onSearch={() => (searchOpen = true)}
          onSettings={() => (settingsOpen = true)}
        />
      </div>
    </div>
    <AudioProgressBar
      sentences={reader.sentences}
      currentIndex={audio.currentIndex}
      isPlaying={reader.isPlaying}
      speed={reader.speed}
    />
  </div>

  <div class="flex-1 overflow-hidden">
    {#if reader.sentences.length > 0}
      <PDFViewer
        {bookId}
        sentences={reader.sentences}
        currentIndex={audio.currentIndex}
        buffering={audio.buffering || seeking}
        pageToScroll={pageToScroll}
        searchMatches={searchMatches}
        currentSearchIndex={currentSearchIndex}
        highlightColor={settings.highlightColor}
        autoscroll={settings.autoscroll}
        onPageChange={(p) => { currentPage = p }}
        onSentenceClick={handleSeek}
      />
    {:else}
      <div class="flex items-center justify-center h-full text-slate-400">
        <svg class="w-5 h-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
        </svg>
        Loading…
      </div>
    {/if}
  </div>
</div>

{#if settingsOpen}
  <SettingsOverlay onClose={() => (settingsOpen = false)} />
{/if}

{#if bookmarksOpen}
  <BookmarkPanel
    {bookId}
    currentSentenceIndex={reader.currentIndex}
    currentSentenceText={reader.sentences[reader.currentIndex]?.text ?? ''}
    onGoToSentence={handleBookmarkGoTo}
    onClose={() => (bookmarksOpen = false)}
  />
{/if}
