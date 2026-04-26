<script lang="ts">
import { onMount, onDestroy } from 'svelte'
import { page } from '$app/stores'
import { get } from 'svelte/store'
import readerStore, { loadBook, seek, setPlaying, setSpeed } from '$lib/stores/reader'
import { audioStore } from '$lib/stores/audio'
import PDFViewer from '$lib/components/PDFViewer.svelte'
import MediaBar from '$lib/components/MediaBar.svelte'
import TopToolbar from '$lib/components/TopToolbar.svelte'
import AudioProgressBar from '$lib/components/AudioProgressBar.svelte'
import PageNavigator from '$lib/components/PageNavigator.svelte'  // Import PageNavigator

const bookId = $page.params.id as string

let reader = $state(get(readerStore))
let audio = $state(get(audioStore))
let seeking = $state(false)
let currentPage = $state(0)  // Add currentPage state
let pageToScroll = $state<number | null>(null)  // Add pageToScroll state

let unsubReader: () => void
let unsubAudio: () => void

onMount(async () => {
  unsubReader = readerStore.subscribe((v) => (reader = v))
  unsubAudio = audioStore.subscribe((v) => (audio = v))
  await loadBook(bookId)
  audioStore.init(bookId)
  audioStore.setSpeed(get(readerStore).speed)
})

onDestroy(() => {
  unsubReader?.()
  unsubAudio?.()
  audioStore.destroy()
})

function handlePageJump(page: number) {
  pageToScroll = page
  setTimeout(() => { pageToScroll = null }, 100)
}

function handlePlay() {
  setPlaying(true)
  audioStore.play(reader.currentIndex)
}

function handlePause() {
  setPlaying(false)
  audioStore.pause()
}

async function handleSeek(index: number) {
  if (seeking) return
  seeking = true
  // Fire audio seek immediately — don't block on HTTP progress save
  if (reader.isPlaying) audioStore.seek(index)
  try {
    await seek(index)  // HTTP: save progress to DB (runs in parallel with audio)
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
</script>

<div class="flex flex-col h-full">
  <!-- Top bar -->
  <div class="border-b border-slate-200 bg-white">
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
          onSave={() => seek(reader.currentIndex)}
          onSearch={() => {}}
          onSettings={() => {}}
        />
      </div>
    </div>
    <!-- Progress bar sits below controls, full width -->
    <AudioProgressBar
      sentences={reader.sentences}
      currentIndex={audio.currentIndex}
      isPlaying={reader.isPlaying}
      speed={reader.speed}
    />
  </div>

  <!-- PDF viewer -->
  <div class="flex-1 overflow-hidden">
    {#if reader.sentences.length > 0}
      <PDFViewer
        {bookId}
        sentences={reader.sentences}
        currentIndex={audio.currentIndex}
        buffering={audio.buffering || seeking}
        pageToScroll={pageToScroll}
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
