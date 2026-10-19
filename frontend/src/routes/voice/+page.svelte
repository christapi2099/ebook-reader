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

  let searchQuery = $state('')
  let langFilter = $state<string | null>(null)
  let genderFilter = $state<string | null>(null)

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
    const engineId = voices.find(v => v.id === id)?.engine ?? 'kokoro'
    settingsStore.setVoiceForEngine(engineId, id)
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

  const availableLangs = $derived(Array.from(new Set(voices.map(v => v.lang))).sort())
  const availableGenders = $derived(Array.from(new Set(voices.map(v => v.gender))).sort())

  const filteredVoices = $derived(
    voices.filter(v => {
      if (langFilter && v.lang !== langFilter) return false
      if (genderFilter && v.gender !== genderFilter) return false
      const q = searchQuery.trim().toLowerCase()
      if (!q) return true
      return v.name.toLowerCase().includes(q) || v.id.toLowerCase().includes(q)
    }),
  )

  /** Grouped by language, in the language's own first-seen order, for a labelled `voice-group` layout like the mockup. */
  const groupedVoices = $derived.by(() => {
    const groups = new Map<string, Voice[]>()
    for (const voice of filteredVoices) {
      const list = groups.get(voice.lang) ?? []
      list.push(voice)
      groups.set(voice.lang, list)
    }
    return Array.from(groups.entries())
  })

  /** A deterministic hue per voice, so avatars carry a stable visual identity instead of one flat color for every card. */
  function avatarHue(voiceId: string): number {
    let hash = 0
    for (let i = 0; i < voiceId.length; i++) hash = (hash * 31 + voiceId.charCodeAt(i)) >>> 0
    return hash % 360
  }

  onMount(fetchVoices)
  onDestroy(releaseAudio)
</script>

<div class="flex min-h-full flex-col">
  <div class="flex-1 p-4 md:p-6">
    <div class="flex flex-wrap items-center justify-between gap-2 mb-6">
      <div class="flex items-center gap-2">
        <h1 class="text-xl md:text-2xl font-bold text-fg">Voices</h1>
        {#if selectedVoice}
          <span class="inline-flex items-center gap-1.5 rounded-full bg-surface-sunken px-3 py-1 text-xs font-medium text-fg-muted">
            Reading voice · {voices.find(v => v.id === selectedVoice)?.name ?? selectedVoice}
          </span>
        {/if}
      </div>
      <Button size="sm" onclick={handleUpload}>+ Upload Voice</Button>
    </div>

  {#if error}
    <div class="mb-4 p-3 bg-danger-soft text-danger rounded-lg text-sm">{error}</div>
  {/if}

  {#if loading}
    <div class="flex items-center gap-2 text-fg-muted">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading voices…
    </div>
  {:else}
    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <label class="relative w-full sm:max-w-xs">
        <span class="sr-only">Search voices</span>
        <svg
          class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle"
          fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z" />
        </svg>
        <input
          type="search"
          placeholder="Search voices"
          bind:value={searchQuery}
          class="w-full min-h-11 rounded-md border border-border bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
        />
      </label>
      <div class="flex flex-wrap items-center gap-2">
        {#if availableGenders.length > 1}
          <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by gender">
            <button
              type="button"
              class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium transition-colors {genderFilter === null ? 'border-accent bg-accent-soft text-accent' : 'border-border text-fg-muted'}"
              aria-pressed={genderFilter === null}
              onclick={() => (genderFilter = null)}
            >
              All genders
            </button>
            {#each availableGenders as g (g)}
              <button
                type="button"
                class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium capitalize transition-colors {genderFilter === g ? 'border-accent bg-accent-soft text-accent' : 'border-border text-fg-muted'}"
                aria-pressed={genderFilter === g}
                onclick={() => (genderFilter = g)}
              >
                {g}
              </button>
            {/each}
          </div>
        {/if}
        {#if availableLangs.length > 1}
          <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by language">
            <button
              type="button"
              class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium transition-colors {langFilter === null ? 'border-accent bg-accent-soft text-accent' : 'border-border text-fg-muted'}"
              aria-pressed={langFilter === null}
              onclick={() => (langFilter = null)}
            >
              All languages
            </button>
            {#each availableLangs as lang (lang)}
              <button
                type="button"
                class="min-h-8 rounded-full border px-3 py-1 text-xs font-medium transition-colors {langFilter === lang ? 'border-accent bg-accent-soft text-accent' : 'border-border text-fg-muted'}"
                aria-pressed={langFilter === lang}
                onclick={() => (langFilter = lang)}
              >
                {langLabel(lang)}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    {#if filteredVoices.length === 0}
      <div class="flex flex-col items-center justify-center py-16 text-center text-fg-subtle">
        <p class="text-lg font-medium text-fg-muted">No voices match your search</p>
        <Button variant="secondary" class="mt-3" onclick={() => { searchQuery = ''; langFilter = null; genderFilter = null }}>
          Clear filters
        </Button>
      </div>
    {:else}
      {#each groupedVoices as [lang, groupVoices] (lang)}
        <section class="mb-6" aria-labelledby={`voice-group-${lang}`}>
          {#if groupedVoices.length > 1}
            <h2 id={`voice-group-${lang}`} class="mb-3 text-sm font-semibold uppercase tracking-wide text-fg-muted">
              {langLabel(lang)}
            </h2>
          {/if}
          <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))">
            {#each groupVoices as voice (voice.id)}
              <div
                class="relative flex items-center gap-3 text-left p-4 rounded-xl border transition-all cursor-pointer {selectedVoice === voice.id ? 'border-accent bg-accent-soft shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_14%,transparent)]' : 'border-border bg-surface hover:border-border-strong hover:shadow-1'}"
                onclick={() => selectVoice(voice.id)}
                role="button"
                tabindex="0"
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectVoice(voice.id) } }}
              >
                {#if !voice.built_in}
                  <!-- raw z-10: raising this above its own card's cover is local
                       stacking, not one of the named layers. -->
                  <button
                    class="absolute top-2 right-2 w-5 h-5 rounded-full bg-surface-sunken hover:bg-danger-soft text-fg-subtle hover:text-danger flex items-center justify-center z-10"
                    onclick={(e) => { e.stopPropagation(); handleDelete(voice.id) }}
                    aria-label="Delete voice"
                  >
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                {/if}
                <div
                  class="w-11 h-11 shrink-0 rounded-full flex items-center justify-center font-semibold text-white"
                  style={`background: hsl(${avatarHue(voice.id)} 55% 50%)`}
                  aria-hidden="true"
                >
                  {voice.name.slice(0, 1).toUpperCase()}
                </div>
                <div class="min-w-0 flex-1">
                  <p class="font-semibold text-fg text-sm truncate">{voice.name}</p>
                  <p class="text-xs text-fg-muted mt-0.5 truncate">
                    {voice.id} · {voice.gender} · {langLabel(voice.lang)}
                    {#if voice.quality} · grade {voice.quality}{/if}
                  </p>
                </div>
                <button
                  class="w-8 h-8 shrink-0 rounded-full bg-surface-sunken hover:bg-accent-soft text-fg-muted hover:text-accent flex items-center justify-center transition-colors"
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
                  <span class="flex h-4 items-end gap-[2px] shrink-0" aria-hidden="true">
                    <span class="w-[3px] h-2 rounded-sm bg-accent motion-safe:animate-pulse"></span>
                    <span class="w-[3px] h-4 rounded-sm bg-accent motion-safe:animate-pulse" style="animation-delay: 150ms"></span>
                    <span class="w-[3px] h-3 rounded-sm bg-accent motion-safe:animate-pulse" style="animation-delay: 300ms"></span>
                  </span>
                {/if}
              </div>
            {/each}
          </div>
        </section>
      {/each}
    {/if}
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
