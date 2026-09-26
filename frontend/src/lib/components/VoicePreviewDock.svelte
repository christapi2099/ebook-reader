<script lang="ts">
  import type { Voice } from '$lib/api'
  import { VOICE_PREVIEW_SAMPLE_TEXT } from '$lib/api'
  import { bucketPeaks, barHeights, playedFraction, WAVEFORM_BUCKETS } from '$lib/utils/waveform'

  /** `loading` until the real audio element is ready to play. */
  type PreviewStatus = 'loading' | 'playing' | 'paused'

  let {
    voice,
    audio,
    bytes,
    status,
    selected = false,
    error = null,
    onTogglePlay,
    onStop,
    onUseVoice,
  }: {
    voice: Voice
    /** The real element the page plays. Its clock drives the strip. */
    audio: HTMLAudioElement | null
    /** The bytes `previewVoice` returned, decoded here for the bar envelope. */
    bytes: ArrayBuffer | null
    status: PreviewStatus
    /** Whether this voice is already the saved default. */
    selected?: boolean
    error?: string | null
    onTogglePlay: () => void
    onStop: () => void
    onUseVoice: () => void
  } = $props()

  /**
   * Decode the preview bytes into one channel of samples. A separate, never
   * resumed AudioContext on purpose: routing the playing element through an
   * analyser would put preview audio at the mercy of autoplay policy, and the
   * envelope of the file is the honest source for the bar heights anyway.
   */
  async function decodeEnvelope(buffer: ArrayBuffer): Promise<Float32Array | null> {
    const Ctor =
      typeof AudioContext !== 'undefined'
        ? AudioContext
        : (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return null
    let ctx: AudioContext | null = null
    try {
      ctx = new Ctor()
      const decoded = await ctx.decodeAudioData(buffer.slice(0))
      return decoded.numberOfChannels > 0 ? decoded.getChannelData(0) : null
    } catch {
      return null
    } finally {
      void ctx?.close().catch(() => {})
    }
  }

  let peaks = $state<number[] | null>(null)

  $effect(() => {
    const buffer = bytes
    if (!buffer) {
      peaks = null
      return
    }
    let cancelled = false
    decodeEnvelope(buffer).then(channel => {
      if (!cancelled) peaks = channel ? bucketPeaks(channel, WAVEFORM_BUCKETS) : null
    })
    return () => {
      cancelled = true
    }
  })

  // No envelope means no amplitude information: a flat trough, never invented bars.
  const heights = $derived(peaks ? barHeights(peaks) : barHeights(new Array(WAVEFORM_BUCKETS).fill(0)))

  let currentTime = $state(0)
  let duration = $state(0)

  function readClock(element: HTMLAudioElement | null): void {
    if (!element) {
      currentTime = 0
      duration = 0
      return
    }
    currentTime = element.currentTime
    // `duration` is NaN until the media metadata arrives; unknown, not zero-length.
    duration = Number.isFinite(element.duration) ? element.duration : 0
  }

  $effect(() => {
    const element = audio
    if (!element) {
      readClock(null)
      return
    }
    const sync = () => readClock(element)
    element.addEventListener('timeupdate', sync)
    element.addEventListener('loadedmetadata', sync)
    element.addEventListener('durationchange', sync)
    element.addEventListener('play', sync)
    element.addEventListener('pause', sync)
    element.addEventListener('ended', sync)
    readClock(element)
    return () => {
      element.removeEventListener('timeupdate', sync)
      element.removeEventListener('loadedmetadata', sync)
      element.removeEventListener('durationchange', sync)
      element.removeEventListener('play', sync)
      element.removeEventListener('pause', sync)
      element.removeEventListener('ended', sync)
    }
  })

  // Only while the element is genuinely playing — a paused preview's strip must
  // not creep forward.
  $effect(() => {
    const element = audio
    if (!element || status !== 'playing') return
    let frame = requestAnimationFrame(function tick() {
      readClock(element)
      frame = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(frame)
  })

  const fraction = $derived(playedFraction(currentTime, duration))
  const percent = $derived(Math.round(fraction * 100))
  const playedBars = $derived(Math.round(fraction * heights.length))

  function formatTime(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
    const whole = Math.floor(seconds)
    const minutes = Math.floor(whole / 60)
    return `${minutes}:${String(whole % 60).padStart(2, '0')}`
  }
</script>

<div
  class="sticky bottom-0 z-dock border-t border-border bg-surface-raised px-4 py-3 shadow-2 md:px-6"
  role="region"
  aria-label="Voice preview"
>
  <div class="flex flex-col gap-3 md:flex-row md:items-center md:gap-5">
    <div class="min-w-0 md:w-56 md:shrink-0">
      <p class="truncate text-sm font-semibold text-fg">{voice.name}</p>
      <p class="truncate text-xs text-fg-muted">{VOICE_PREVIEW_SAMPLE_TEXT}</p>
    </div>

    <div
      class="min-w-0 flex-1"
      role="progressbar"
      aria-label="Preview progress"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={percent}
      data-testid="preview-progress"
    >
      <div class="flex h-12 items-end gap-[2px]" aria-hidden="true">
        {#each heights as height, index}
          <span
            class="flex-1 rounded-sm transition-colors {index < playedBars ? 'bg-accent' : 'bg-border-strong'}"
            style="height: {height}%"
          ></span>
        {/each}
      </div>
      <div class="mt-1 flex justify-between text-xs tabular-nums text-fg-muted">
        <span>{formatTime(currentTime)}</span>
        <span>{formatTime(duration)}</span>
      </div>
    </div>

    <div class="flex items-center gap-2 md:shrink-0">
      <button
        type="button"
        class="flex h-11 w-11 items-center justify-center rounded-full bg-surface-sunken text-fg transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
        aria-label={status === 'playing' ? 'Pause preview' : 'Play preview'}
        data-loading={status === 'loading' ? 'true' : 'false'}
        disabled={status === 'loading'}
        onclick={onTogglePlay}
      >
        {#if status === 'loading'}
          <svg class="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
        {:else if status === 'playing'}
          <svg class="h-5 w-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="6" y="4" width="4" height="16" rx="1" />
            <rect x="14" y="4" width="4" height="16" rx="1" />
          </svg>
        {:else}
          <svg class="h-5 w-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
        {/if}
      </button>

      <button
        type="button"
        class="flex h-11 min-w-11 items-center justify-center rounded-full bg-surface-sunken px-3 text-sm font-medium text-fg transition-colors hover:bg-accent-soft"
        aria-label="Stop preview"
        onclick={onStop}
      >
        <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
        </svg>
      </button>

      <button
        type="button"
        class="min-h-11 rounded-lg px-4 text-sm font-medium transition-colors {selected ? 'bg-accent-soft text-accent' : 'bg-accent text-accent-fg hover:bg-accent-hover'}"
        aria-pressed={selected}
        onclick={onUseVoice}
      >
        {selected ? 'Using this voice' : 'Use this voice'}
      </button>
    </div>
  </div>

  {#if error}
    <p class="mt-2 text-sm text-danger" role="alert">{error}</p>
  {/if}
</div>
