<script lang="ts">
  let {
    isPlaying,
    speed,
    disabled = false,
    buffering = false,
    onPlay,
    onPause,
    onRewind,
    onForward,
    onSpeedChange,
  }: {
    isPlaying: boolean
    speed: number
    disabled?: boolean
    buffering?: boolean
    onPlay: () => void
    onPause: () => void
    onRewind: () => void
    onForward: () => void
    onSpeedChange: (s: number) => void
  } = $props()

  const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

  // Rewind/forward move whole sentences, so the visible text says sentences too —
  // WCAG 2.5.3 requires the accessible name to contain the visible label.
  const STEP_SENTENCES = 5

  const playbackStatus = $derived(
    isPlaying && buffering ? 'Buffering audio' : isPlaying ? 'Playing' : 'Paused'
  )
</script>

<div class="flex flex-col items-center gap-2 w-full">
  <span class="sr-only" role="status" aria-live="polite">{playbackStatus}</span>
  <div class="flex items-center justify-center gap-4">
    <button
      onclick={onRewind}
      {disabled}
      class="flex items-center gap-1 px-4 py-3 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium transition-colors min-h-[44px] disabled:opacity-40 disabled:cursor-not-allowed"
      aria-label={`Rewind ${STEP_SENTENCES} sentences`}
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.333 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z" />
      </svg>
      {STEP_SENTENCES}
    </button>

    <button
      onclick={() => (isPlaying ? onPause() : onPlay())}
      class="flex items-center justify-center w-12 h-12 rounded-full bg-blue-500 hover:bg-blue-600 text-white shadow-lg transition-colors"
      aria-label={isPlaying ? 'Pause' : 'Play'}
      data-loading={isPlaying && buffering ? 'true' : 'false'}
    >
      {#if isPlaying && buffering}
        <svg class="w-6 h-6 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
        </svg>
      {:else if isPlaying}
        <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
          <rect x="6" y="4" width="4" height="16" rx="1" />
          <rect x="14" y="4" width="4" height="16" rx="1" />
        </svg>
      {:else}
        <svg class="w-6 h-6 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M8 5v14l11-7z" />
        </svg>
      {/if}
    </button>

    <button
      onclick={onForward}
      {disabled}
      class="flex items-center gap-1 px-4 py-3 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium transition-colors min-h-[44px] disabled:opacity-40 disabled:cursor-not-allowed"
      aria-label={`Forward ${STEP_SENTENCES} sentences`}
    >
      {STEP_SENTENCES}
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M11.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 005 8v8a1 1 0 001.6.8l5.334-4zM19.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.334-4z" />
      </svg>
    </button>
  </div>

  <div class="flex items-center gap-0.5 bg-slate-100 rounded-full px-1 py-1" role="group" aria-label="Playback speed">
    {#each speeds as s}
      <button
        onclick={() => onSpeedChange(s)}
        class="px-2.5 py-1 rounded-full text-xs font-semibold transition-colors {speed === s ? 'bg-blue-500 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-200'}"
        aria-pressed={speed === s}
      >
        {s}x
      </button>
    {/each}
  </div>
</div>
