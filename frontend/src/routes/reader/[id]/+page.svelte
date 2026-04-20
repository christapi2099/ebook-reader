<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { page } from '$app/stores'
  import { get } from 'svelte/store'
  import readerStore, { loadBook, seek, setPlaying, setSpeed } from '$lib/stores/reader'
  import { audioStore } from '$lib/stores/audio'
  import PDFViewer from '$lib/components/PDFViewer.svelte'
  import MediaBar from '$lib/components/MediaBar.svelte'
  import TopToolbar from '$lib/components/TopToolbar.svelte'

  const bookId = $page.params.id as string

  let reader = $state(get(readerStore))
  let audio = $state(get(audioStore))
  let seeking = $state(false)

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
    try {
      await seek(index)
      if (reader.isPlaying) audioStore.seek(index)
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
  <!-- Top bar: stacks vertically on mobile -->
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between px-3 py-2 border-b border-slate-200 bg-white gap-2">
    <MediaBar
      isPlaying={reader.isPlaying}
      speed={reader.speed}
      onPlay={handlePlay}
      onPause={handlePause}
      onRewind={handleRewind}
      onForward={handleForward}
      onSpeedChange={handleSpeedChange}
    />
    <TopToolbar
      onToggleCC={() => {}}
      onCopyText={() => {}}
      onSave={() => seek(reader.currentIndex)}
      onSearch={() => {}}
      onSettings={() => {}}
    />
  </div>

  <!-- PDF viewer -->
  <div class="flex-1 overflow-hidden">
    {#if reader.sentences.length > 0}
      <PDFViewer
        {bookId}
        filePath=""
        sentences={reader.sentences}
        currentIndex={reader.currentIndex}
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
