<script lang="ts">
import { onMount, onDestroy } from 'svelte'
import { page } from '$app/stores'
import { get } from 'svelte/store'
import readerStore, {
  flushProgress,
  loadBook,
  mirrorPlaybackIndex,
  seek,
  setPlaying,
  setSpeed,
  type ReaderState,
} from '$lib/stores/reader'
import { audioStore, type AudioState } from '$lib/stores/audio'
import { settingsStore, type SettingsState } from '$lib/stores/settings'
import { createBookmark, getBook } from '$lib/api'
import { registerHotkeys, unregisterHotkeys } from '$lib/utils/hotkeys'
import { buildPageIndex, pageForSentenceIndex } from '$lib/utils/page-index'
import PDFViewer from '$lib/components/PDFViewer.svelte'
import TextViewer from '$lib/components/TextViewer.svelte'
import MediaBar from '$lib/components/MediaBar.svelte'
import TopToolbar from '$lib/components/TopToolbar.svelte'
import AudioProgressBar from '$lib/components/AudioProgressBar.svelte'
import PageNavigator from '$lib/components/PageNavigator.svelte'
import PagesOverlay from '$lib/components/PagesOverlay.svelte'
import SettingsOverlay from '$lib/components/SettingsOverlay.svelte'
import SearchOverlay from '$lib/components/SearchOverlay.svelte'
import BookmarkPanel from '$lib/components/BookmarkPanel.svelte'
import { goto } from '$app/navigation'

const bookId = $page.params.id as string

let reader: ReaderState = $state(get(readerStore))
let audio = $state(get(audioStore))
  let settings: SettingsState = $state(get(settingsStore))
let seeking = $state(false)
let currentPage = $state(0)
let pageToScroll = $state<number | null>(null)
let settingsOpen = $state(false)
let searchOpen = $state(false)
let bookmarksOpen = $state(false)
let pagesOpen = $state(false)

let bookMeta = $state<{file_type: string; title: string; page_count: number} | null>(null)
let totalPages = $derived(bookMeta?.page_count ?? reader.sentences.length)

// PDFs number their own pages; every other format has one page per sentence.
const isPageBased = $derived(bookMeta?.file_type === 'pdf')
/** Every page of the book and the sentence it starts at — the skim overlay's list. */
let pageIndex = $derived(buildPageIndex(reader.sentences, totalPages, isPageBased))

// Search state
let searchMatches = $state<number[]>([])
let currentSearchIndex = $state(-1)

let unsubReader: () => void
let unsubAudio: () => void
let unsubSettings: () => void

onMount(async () => {
  unsubReader = readerStore.subscribe((v: ReaderState) => (reader = v))
  unsubAudio = audioStore.subscribe((v: AudioState) => (audio = v))
  unsubSettings = settingsStore.subscribe((v: SettingsState) => (settings = v))

  // Fetch book metadata for type detection
  try {
    bookMeta = await getBook(bookId)
  } catch (e) {
    console.error('Failed to fetch book metadata:', e)
  }

  await loadBook(bookId)
  settingsStore.loadFromServer().catch(() => {})
  audioStore.init(bookId)
  audioStore.setSpeed((get(readerStore) as ReaderState).speed)
  audioStore.setCurrentIndex((get(readerStore) as ReaderState).currentIndex)
  audioStore.setVoice(get(settingsStore).voice)

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
    // Escape is deliberately absent: the overlay stack routes it to the topmost
    // open layer only, so it must not be handled here as a blanket "close all".
  }
  registerHotkeys(hotkeyMap)

  // The position has to survive the page going away, which a throttled write
  // cannot promise by itself. `pagehide` covers a real unload and a bfcache
  // freeze; `visibilitychange` covers a mobile app being backgrounded, which can
  // happen with no `pagehide` at all.
  window.addEventListener('pagehide', handlePageHide)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onDestroy(() => {
  // Before anything is torn down: leaving the reader is the other moment the
  // throttled write could lose the position.
  flushProgress()
  window.removeEventListener('pagehide', handlePageHide)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  unsubReader?.()
  unsubAudio?.()
  unsubSettings?.()
  audioStore.destroy()
  unregisterHotkeys()
})

/** A real unload or a bfcache freeze — a normal request would die with the page. */
function handlePageHide() {
  flushProgress(true)
}

/** Backgrounding, which on mobile can arrive without any `pagehide`. */
function handleVisibilityChange() {
  if (document.visibilityState === 'hidden') flushProgress(true)
}

/**
 * Jump to a 0-based page. Both the page indicator and the skim overlay come
 * through here, and both end at `seek()` — the route's one way to move the
 * reading position, so the page, the highlight, the audio and the saved
 * progress never disagree.
 */
function handlePageJump(page: number) {
  const entry = pageIndex.find(e => e.page === page)
  if (!entry) return
  // PDFs keep the existing page-level scroll affordance as well; the sentence
  // seek drives the text view, the highlight and everything else.
  if (isPageBased) {
    pageToScroll = page
    setTimeout(() => { pageToScroll = null }, 100)
  }
  void handleSeek(entry.sentenceIndex)
}

/** The indicator follows the reader's own position, whatever moved it. */
$effect(() => {
  const page = pageForSentenceIndex(pageIndex, reader.currentIndex)
  if (page !== null) currentPage = page
})

$effect(() => {
  if (!audio.isPlaying && reader.isPlaying) {
    setPlaying(false)
    // Playback ended — the reader paused, or the book finished. Either way the
    // position should be durable now rather than up to five seconds from now.
    flushProgress()
  }
})

// The audio store knows where playback is; this store knows where the user asked
// to be. While playing, the first is the truth — mirroring it is what keeps the
// page indicator and the saved position on the sentence being heard, instead of
// on the last one that was clicked.
$effect(() => {
  mirrorPlaybackIndex(audio.currentIndex, audio.isPlaying)
})

$effect(() => {
  audioStore.setVoice(settings.voice)
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
  // Explicitly, because clearing `isPlaying` above stops the playback-ended
  // effect from firing — and a pause is exactly when the position must be safe.
  flushProgress()
}

async function handleSeek(index: number) {
  if (seeking) return
  seeking = true

  if (reader.isPlaying) {
    audioStore.seek(index)
  } else {
    // Auto-play from the clicked sentence and fix the stale audio.currentIndex bug
    setPlaying(true)
    audioStore.play(index)
  }
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

function handleBackToLibrary() {
  goto('/library')
}
</script>

<div class="flex flex-col h-full">
  <div class="border-b border-border bg-surface">
    {#if searchOpen}
      <SearchOverlay
        sentences={reader.sentences}
        onClose={() => { searchOpen = false; searchMatches = []; currentSearchIndex = -1 }}
        onResults={handleSearchResults}
      />
    {/if}
    <div class="grid grid-cols-3 items-center px-3 py-2">
      <div class="flex items-center justify-center gap-2">
        <!-- Back button -->
        <button
          onclick={handleBackToLibrary}
          class="flex h-11 w-11 items-center justify-center rounded-md hover:bg-surface-sunken text-fg-muted"
          aria-label="Back to library"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
          </svg>
        </button>
        <PageNavigator currentPage={currentPage} totalPages={totalPages} onGoToPage={handlePageJump} />
        <button
          type="button"
          class="flex min-h-11 items-center gap-1.5 rounded-md px-2 text-sm text-fg-muted hover:bg-surface-sunken hover:text-fg disabled:opacity-40"
          aria-haspopup="dialog"
          aria-expanded={pagesOpen}
          disabled={pageIndex.length === 0}
          onclick={() => (pagesOpen = true)}
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"/>
          </svg>
          Pages
        </button>
      </div>
      <div class="flex justify-center">
    <MediaBar
      isPlaying={reader.isPlaying}
      speed={reader.speed}
      disabled={seeking}
      buffering={audio.buffering}
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
      elapsedSeconds={audio.elapsedSeconds}
      sentenceDurations={audio.sentenceDurations}
      isPlaying={audio.isPlaying}
      buffering={audio.buffering}
    />
  </div>

  <div class="flex-1 overflow-hidden">
    {#if reader.sentences.length > 0}
      {#if bookMeta?.file_type === 'text' || bookMeta?.file_type === 'epub'}
        <!-- Text books (saved from text reader) or EPUBs get TextViewer -->
        <TextViewer
          sentences={reader.sentences}
          currentIndex={audio.currentIndex}
          currentWordIndex={audio.currentWordIndex}
          isPlaying={audio.isPlaying}
          highlightColor={settings.highlightColor}
          highlightEnabled={settings.highlightEnabled}
          buffering={audio.buffering}
          autoscroll={settings.autoscroll}
          bionicMode={settings.bionicMode}
          bionicFixation={settings.bionicFixation}
          bionicBoldRatio={settings.bionicBoldRatio}
          onSentenceClick={handleSeek}
        />
      {:else}
        <!-- PDF books get PDFViewer -->
        <PDFViewer
          {bookId}
          sentences={reader.sentences}
          currentIndex={audio.currentIndex}
          currentWordIndex={audio.currentWordIndex}
          buffering={audio.buffering || seeking}
          pageToScroll={pageToScroll}
          searchMatches={searchMatches}
          currentSearchIndex={currentSearchIndex}
          highlightColor={settings.highlightColor}
          highlightEnabled={settings.highlightEnabled}
          autoscroll={settings.autoscroll}
          bionicMode={settings.bionicMode}
          bionicFixation={settings.bionicFixation}
          bionicBoldRatio={settings.bionicBoldRatio}
          onPageChange={(p) => { currentPage = p }}
          onSentenceClick={handleSeek}
        />
      {/if}
    {:else}
      <div class="flex items-center justify-center h-full text-fg-subtle">
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

{#if pagesOpen}
  <PagesOverlay
    pages={pageIndex}
    {currentPage}
    bookTitle={bookMeta?.title ?? 'This book'}
    onJump={(page) => { pagesOpen = false; handlePageJump(page) }}
    onClose={() => (pagesOpen = false)}
  />
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
