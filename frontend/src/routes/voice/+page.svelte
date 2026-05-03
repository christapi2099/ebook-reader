<script lang="ts">
  import { onMount } from 'svelte'
  import { get } from 'svelte/store'
  import { getVoices, uploadVoice, deleteVoice, previewVoice, API_BASE } from '$lib/api'
  import type { Voice } from '$lib/api'
  import { settingsStore } from '$lib/stores/settings'

  let voices = $state<Voice[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let selectedVoice = $state(get(settingsStore).voice)
  let previewingId: string | null = $state(null)
  let currentAudio: HTMLAudioElement | null = null

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

  async function handlePreview(voiceId: string) {
    if (previewingId === voiceId) {
      currentAudio?.pause()
      previewingId = null
      return
    }
    currentAudio?.pause()
    previewingId = voiceId
    try {
      const buf = await previewVoice(voiceId)
      const blob = new Blob([buf], { type: 'audio/wav' })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { previewingId = null; URL.revokeObjectURL(url) }
      currentAudio = audio
      audio.play()
    } catch {
      previewingId = null
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
      voices = voices.filter(v => v.id !== voiceId)
    } catch (e: any) {
      error = e?.message ?? 'Delete failed'
    }
  }

  function langLabel(lang: string): string {
    const labels: Record<string, string> = { 'en-US': 'American', 'en-GB': 'British' }
    return labels[lang] || lang
  }

  onMount(fetchVoices)
</script>

<div class="p-4 md:p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-xl md:text-2xl font-bold text-slate-800">Voices</h1>
    <button
      class="px-3 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 font-medium transition-colors text-sm"
      onclick={handleUpload}
    >
      + Upload Voice
    </button>
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
          onkeydown={(e) => { if (e.key === 'Enter') selectVoice(voice.id) }}
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
              onclick={(e) => { e.stopPropagation(); handlePreview(voice.id) }}
              aria-label="Preview voice"
            >
              {#if previewingId === voice.id}
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
              {:else}
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
              {/if}
            </button>
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
