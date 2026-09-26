<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { getVoices, uploadVoice, deleteVoice, previewVoice } from '$lib/api'
  import type { Voice } from '$lib/api'
  import { settingsStore } from '$lib/stores/settings'
  import VoicePreviewDock from '$lib/components/VoicePreviewDock.svelte'
  import Button from '$lib/ui/Button.svelte'

  /** `loading` until the real audio element is ready to play. */
  type PreviewStatus = 'loading' | 'playing' | 'paused'

  let voices = $state<Voice[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let selectedVoice = $state(get(settingsStore).voice)

  // Preview. The audio path is the one that was already here — real bytes from
  // previewVoice, one object URL, one HTMLAudioElement — extended with a dock
  // that controls and visualises that same element.
  let previewVoiceId = $state<string | null>(null)
  let previewStatus = $state<PreviewStatus>('loading')
  let previewAudio = $state<HTMLAudioElement | null>(null)
  let previewBytes = $state<ArrayBuffer | null>(null)
  let previewError = $state<string | null>(null)
  let currentAudioUrl: string | null = null
  // Bumped whenever the preview target changes, so a slow fetch for a voice the
  // user has already moved on from cannot hijack the dock.
  let previewToken = 0

  async function fetchVoices() {
    loading = true
    error = null
    try {
      voices = await getVoices()
    } catch (e: any) {
      error = 'Could not load voices. Make sure the backend is running.'
    } finally {
      loading = false
    }
  }

  function selectVoice(id: string) {
    selectedVoice = id
    settingsStore.setVoice(id)
  }

  function releaseAudio() {
    previewToken++
    if (previewAudio) {
      previewAudio.pause()
      previewAudio.removeAttribute('src')
    }
    if (currentAudioUrl) {
      URL.revokeObjectURL(currentAudioUrl)
      currentAudioUrl = null
    }
    previewAudio = null
    previewBytes = null
  }

  function stopPreview() {
    releaseAudio()
    previewVoiceId = null
    previewError = null
    previewStatus = 'paused'
  }

  /**
   * Drive the dock's state from the element's own events rather than from the
   * click, so a play() the browser refuses cannot leave the UI saying "playing".
   */
  function attachHandlers(audio: HTMLAudioElement, token: number) {
    audio.onplay = () => {
      if (token === previewToken) previewStatus = 'playing'
    }
    audio.onpause = () => {
      if (token === previewToken && !audio.ended) previewStatus = 'paused'
    }
    audio.onended = () => {
      if (token !== previewToken) return
      previewStatus = 'paused'
      // Rewound rather than released: the object URL stays valid until the
      // preview is stopped or replaced, so Play restarts the same sample.
      audio.currentTime = 0
    }
    audio.onerror = () => {
      if (token !== previewToken) return
      previewStatus = 'paused'
      previewError = 'The preview audio could not be decoded.'
    }
  }

  async function startPreview(voiceId: string) {
    if (previewVoiceId === voiceId && previewAudio) {
      togglePreviewPlayback()
      return
    }
    stopPreview()
    const token = ++previewToken
    previewVoiceId = voiceId
    previewStatus = 'loading'
    try {
      const buf = await previewVoice(voiceId)
      if (token !== previewToken) return
      previewBytes = buf
      const url = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }))
      currentAudioUrl = url
      const audio = new Audio(url)
      previewAudio = audio
      attachHandlers(audio, token)
      await audio.play()
    } catch (e: any) {
      if (token !== previewToken) return
      previewError = e?.message
        ? `Could not play this preview · ${e.message}`
        : 'Could not play this preview · the backend did not return audio'
    }
  }

  function togglePreviewPlayback() {
    if (!previewVoiceId) return
    const audio = previewAudio
    if (!audio) {
      // A failed load leaves the dock up with the reason, and Play retries it.
      void startPreview(previewVoiceId)
      return
    }
    if (audio.paused) {
      audio.play().catch(() => {
        previewError = 'The browser blocked playback. Press Play again.'
      })
    } else {
      audio.pause()
    }
  }

  async function handleUpload() {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.pt'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        await uploadVoice(file)
        await fetchVoices()
      } catch (e: any) {
        error = e?.message ?? 'Upload failed'
      }
    }
    input.click()
  }

  async function handleDelete(voiceId: string) {
    try {
      await deleteVoice(voiceId)
      if (previewVoiceId === voiceId) stopPreview()
      voices = voices.filter(v => v.id !== voiceId)
    } catch (e: any) {
      error = e?.message ?? 'Delete failed'
    }
  }

  function langLabel(lang: string): string {
    const labels: Record<string, string> = { 'en-US': 'American', 'en-GB': 'British' }
    return labels[lang] || lang
  }

  const previewTarget = $derived(voices.find(v => v.id === previewVoiceId) ?? null)

  onMount(fetchVoices)
  onDestroy(releaseAudio)
</script>

<div class="flex min-h-full flex-col">
  <div class="flex-1 p-4 md:p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-xl md:text-2xl font-bold text-slate-800">Voices</h1>
      <Button size="sm" onclick={handleUpload}>+ Upload Voice</Button>
    </div>

  {#if error}
    <div class="mb-4 p-3 bg-red-50 text-red-600 rounded-lg text-sm">{error}</div>
  {/if}

  {#if loading}
    <div class="flex items-center gap-2 text-slate-500">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading voices…
    </div>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
      {#each voices as voice (voice.id)}
        <div
          class="relative text-left p-4 rounded-xl border-2 transition-all cursor-pointer {selectedVoice === voice.id ? 'border-blue-500 bg-blue-50 shadow-md' : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'}"
          onclick={() => selectVoice(voice.id)}
          role="button"
          tabindex="0"
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectVoice(voice.id) } }}
        >
          {#if !voice.built_in}
            <button
              class="absolute top-2 right-2 w-5 h-5 rounded-full bg-slate-100 hover:bg-red-100 text-slate-400 hover:text-red-500 flex items-center justify-center z-10"
              onclick={(e) => { e.stopPropagation(); handleDelete(voice.id) }}
              aria-label="Delete voice"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          {/if}
          <div class="flex items-center gap-2 mb-2">
            <div class="w-10 h-10 rounded-full bg-gradient-to-br from-blue-200 to-purple-200 flex items-center justify-center">
              <svg class="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m-3-12h3m-3 3h3" />
              </svg>
            </div>
            <button
              class="w-8 h-8 rounded-full bg-slate-100 hover:bg-blue-100 text-slate-500 hover:text-blue-600 flex items-center justify-center transition-colors"
              onclick={(e) => { e.stopPropagation(); startPreview(voice.id) }}
              aria-label={previewVoiceId === voice.id && previewStatus === 'playing'
                ? `Pause preview of ${voice.name}`
                : `Preview ${voice.name}`}
            >
              {#if previewVoiceId === voice.id && previewStatus === 'playing'}
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
              {:else}
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
              {/if}
            </button>
            {#if previewVoiceId === voice.id && previewStatus === 'playing'}
              <!-- Shows that this card's preview is really playing; it is not a level meter. -->
              <span class="flex h-4 items-end gap-[2px]" aria-hidden="true">
                <span class="w-[3px] h-2 rounded-sm bg-blue-500 motion-safe:animate-pulse"></span>
                <span class="w-[3px] h-4 rounded-sm bg-blue-500 motion-safe:animate-pulse" style="animation-delay: 150ms"></span>
                <span class="w-[3px] h-3 rounded-sm bg-blue-500 motion-safe:animate-pulse" style="animation-delay: 300ms"></span>
              </span>
            {/if}
          </div>
          <p class="font-semibold text-slate-800 text-sm">{voice.name}</p>
          <p class="text-xs text-slate-500 mt-0.5">{voice.id}</p>
          <div class="flex items-center gap-2 mt-1.5">
            <span class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{voice.gender}</span>
            <span class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{langLabel(voice.lang)}</span>
          </div>
        </div>
      {/each}
    </div>
  {/if}
  </div>

  {#if previewTarget}
    <VoicePreviewDock
      voice={previewTarget}
      audio={previewAudio}
      bytes={previewBytes}
      status={previewStatus}
      selected={selectedVoice === previewTarget.id}
      error={previewError}
      onTogglePlay={togglePreviewPlayback}
      onStop={stopPreview}
      onUseVoice={() => selectVoice(previewTarget.id)}
    />
  {/if}
</div>
