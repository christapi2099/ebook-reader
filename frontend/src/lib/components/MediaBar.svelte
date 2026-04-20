<script lang="ts">
  let { isPlaying, speed, onPlay, onPause, onRewind, onForward, onSpeedChange }: {
    isPlaying: boolean
    speed: number
    onPlay: () => void
    onPause: () => void
    onRewind: () => void
    onForward: () => void
    onSpeedChange: (s: number) => void
  } = $props()

  const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

  function handleSpeedClick() {
    const idx = speeds.indexOf(speed)
    const next = speeds[(idx + 1) % speeds.length]
    onSpeedChange(next)
  }
</script>

<div class="flex items-center justify-center gap-4">
  <button
    onclick={onRewind}
    class="flex items-center gap-1 px-4 py-3 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium transition-colors min-h-[44px]"
    aria-label="Rewind 5 sentences"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.333 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z" />
    </svg>
    15s
  </button>

  <button
    onclick={() => (isPlaying ? onPause() : onPlay())}
    class="flex items-center justify-center w-12 h-12 rounded-full bg-blue-500 hover:bg-blue-600 text-white shadow-lg transition-colors"
    aria-label={isPlaying ? 'Pause' : 'Play'}
  >
    {#if isPlaying}
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
    class="flex items-center gap-1 px-4 py-3 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium transition-colors min-h-[44px]"
    aria-label="Forward 5 sentences"
  >
    15s
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M11.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 005 8v8a1 1 0 001.6.8l5.334-4zM19.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.334-4z" />
    </svg>
  </button>

  <button
    onclick={handleSpeedClick}
    class="px-3 py-1.5 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-semibold transition-colors min-w-[52px]"
    aria-label="Change speed"
  >
    {speed}x
  </button>
</div>
