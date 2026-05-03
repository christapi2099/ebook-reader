<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import { createTextBook, getSentences, persistTextBook, type Sentence } from '$lib/api'
  import { audioStore } from '$lib/stores/audio'
  import { settingsStore } from '$lib/stores/settings'
  import MediaBar from '$lib/components/MediaBar.svelte'
  import AudioProgressBar from '$lib/components/AudioProgressBar.svelte'
  import TextViewer from '$lib/components/TextViewer.svelte'
  import UploadDialog from '$lib/components/UploadDialog.svelte'
  import { get } from 'svelte/store'

  let textarea = $state('')
  let isSubmitting = $state(false)
  let error = $state<string | null>(null)

  // Reading state
  let mode = $state<'idle' | 'reading'>('idle')
  let bookId = $state<string | null>(null)
  let sentences = $state<Sentence[]>([])
  let isSaved = $state(false)
  let uploadOpen = $state(false)

  // Subscribe to audio store for playback state
  let audio = $state(get(audioStore))
  onMount(() => {
    return audioStore.subscribe(v => audio = v)
  })

  // Subscribe to settings for voice/color
  let settings = $state(get(settingsStore))
  onMount(() => {
    return settingsStore.subscribe(v => settings = v)
  })

  async function handleSubmit() {
    if (!textarea.trim()) return

    isSubmitting = true
    error = null

    try {
      const result = await createTextBook(textarea.trim())
      bookId = result.book_id
      isSaved = false

      // Fetch sentences for display
      sentences = await getSentences(bookId)

      audioStore.init(bookId!)
      audioStore.setVoice(settings.voice)
      audioStore.setCurrentIndex(0)

      mode = 'reading'
    } catch (e: any) {
      error = e?.message || 'Failed to create text book'
    } finally {
      isSubmitting = false
    }
  }

  function handleNewText() {
    audioStore.destroy()
    mode = 'idle'
    textarea = ''
    bookId = null
    sentences = []
    isSaved = false
    error = null
  }

  async function handleSave() {
    if (!bookId) return
    try {
      await persistTextBook(bookId)
      isSaved = true
    } catch (e: any) {
      error = e?.message || 'Failed to save text'
    }
  }

  // Media control handlers
  function handlePlay() {
    if (!bookId) return
    audioStore.play(audio.currentIndex)
  }

  function handlePause() {
    audioStore.pause()
  }

  function handleSeek(index: number) {
    if (!bookId) return
    audioStore.seek(index)
  }

  function handleSpeedChange(speed: number) {
    audioStore.setSpeed(speed)
  }

  function handleRewind() {
    handleSeek(Math.max(0, audio.currentIndex - 5))
  }

  function handleForward() {
    handleSeek(Math.min(sentences.length - 1, audio.currentIndex + 5))
  }

  function handleUploaded(bookId: string, _title: string) {
    uploadOpen = false
    goto(`/reader/${bookId}`)
  }
</script>

<!-- Idle State: Text Input -->
{#if mode === 'idle'}
  <div class="flex flex-col h-full">
    <div class="flex-1 flex flex-col items-center justify-center p-4 md:p-8">
      <div class="w-full max-w-3xl space-y-6">
        <!-- Header with upload button -->
        <div class="flex items-center justify-between mb-4">
          <h1 class="text-2xl font-bold text-slate-800">Text Reader</h1>
          <button
            onclick={() => uploadOpen = true}
            class="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 flex items-center gap-2"
            aria-label="Upload PDF/EPUB"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
            </svg>
            Upload
          </button>
        </div>

        <!-- Text area -->
        <div class="space-y-3">
          <textarea
            bind:value={textarea}
            placeholder="Paste or type your text here..."
            class="w-full min-h-[200px] max-h-[50vh] p-4 border border-slate-300 rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-base leading-relaxed"
            disabled={isSubmitting}
          ></textarea>
        </div>

        {#if error}
          <div class="text-red-500 text-sm">{error}</div>
        {/if}

        <!-- Play button -->
        <div class="flex justify-center">
          <button
            onclick={handleSubmit}
            disabled={!textarea.trim() || isSubmitting}
            class="px-8 py-3 bg-blue-500 text-white rounded-full text-lg font-medium hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {#if isSubmitting}
              <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
              </svg>
            {:else}
              <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z"/>
              </svg>
            {/if}
            Read Aloud
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- Reading State: TextViewer + Controls -->
{:else}
  <div class="flex flex-col h-full">
    <!-- Header -->
    <div class="border-b border-slate-200 bg-white">
      <div class="flex items-center justify-between px-3 py-2">
        <div class="flex items-center gap-2">
          <button
            onclick={handleNewText}
            class="p-2 rounded-md hover:bg-slate-100 text-slate-600"
            aria-label="New text"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
            </svg>
          </button>
          <button
            onclick={() => uploadOpen = true}
            class="p-2 rounded-md hover:bg-slate-100 text-slate-600"
            aria-label="Upload file"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
            </svg>
          </button>
        </div>
        <div class="flex items-center gap-2">
          {#if !isSaved}
            <button
              onclick={handleSave}
              class="px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-50 rounded-md"
              aria-label="Save to library"
            >
              Save
            </button>
          {:else}
            <span class="px-3 py-1.5 text-sm text-green-600 flex items-center gap-1">
              <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
              </svg>
              Saved
            </span>
          {/if}
          <!-- Voice selector would go here -->
        </div>
      </div>

      <!-- Audio progress bar -->
      <AudioProgressBar
        sentences={sentences}
        currentIndex={audio.currentIndex}
        isPlaying={audio.isPlaying}
        speed={audio.speed}
      />
    </div>

    <!-- Media controls -->
    <div class="border-b border-slate-200 bg-white">
      <div class="flex items-center justify-center gap-2 py-2 px-2">
        <MediaBar
          isPlaying={audio.isPlaying}
          speed={audio.speed}
          disabled={audio.buffering}
          onPlay={handlePlay}
          onPause={handlePause}
          onRewind={handleRewind}
          onForward={handleForward}
          onSpeedChange={handleSpeedChange}
        />
      </div>
    </div>

    <!-- TextViewer -->
    {#if sentences.length > 0}
      <TextViewer
        sentences={sentences}
        currentIndex={audio.currentIndex}
        isPlaying={audio.isPlaying}
        highlightColor={settings.highlightColor}
        autoscroll={settings.autoscroll}
        onSentenceClick={handleSeek}
      />
    {:else}
      <div class="flex-1 flex items-center justify-center text-slate-400">
        Loading sentences...
      </div>
    {/if}
  </div>
{/if}

<!-- Upload Dialog -->
<UploadDialog
  open={uploadOpen}
  onClose={() => uploadOpen = false}
  onUploaded={handleUploaded}
/>
