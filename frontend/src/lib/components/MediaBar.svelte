<script lang="ts">
  import { speedDowngradeStore } from '$lib/stores/audio'

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

  // 3.0 is deliberately absent: Kokoro's duration predictor floors every phoneme
  // at one 25 ms frame (`clamp(min=1)` in kokoro/model.py:108), so the delivered
  // rate saturates well below the request. Measured twice on this codebase, 3.0
  // renders at roughly 2.15-2.2x — about 27% short of its own label — and on some
  // sentences is identical to 2.75x. Offering it would be a control that lies
  // about what it does. 2.0 is imperfect too (it delivers ~1.8-1.9x) but the
  // shortfall is an order of magnitude less misleading and it is the top of the
  // range Kokoro's own author exposes.
  const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

  // Rewind/forward move whole sentences, so the visible text says sentences too —
  // WCAG 2.5.3 requires the accessible name to contain the visible label.
  const STEP_SENTENCES = 5

  const playbackStatus = $derived(
    isPlaying && buffering ? 'Buffering audio' : isPlaying ? 'Playing' : 'Paused'
  )

  // Read straight from the store rather than a prop: the reader route passes the
  // reader store's speed and is owned by another change, but the highlighted rate
  // has to be the rate the engine is actually rendering.
  const downgrade = $derived($speedDowngradeStore)
  const activeSpeed = $derived(downgrade?.effective ?? speed)
  const speedLocked = $derived(downgrade !== null)

  // Dismissing hides the explanation, not the fact — a new refusal brings a new
  // notice back.
  const noticeKey = $derived(downgrade ? `${downgrade.requested}→${downgrade.effective}` : null)
  let dismissedKey = $state<string | null>(null)
  const showNotice = $derived(noticeKey !== null && noticeKey !== dismissedKey)
</script>

<div class="flex flex-col items-center gap-2 w-full">
  <span class="sr-only" role="status" aria-live="polite">{playbackStatus}</span>

  {#if showNotice && downgrade}
    <div
      class="flex items-start gap-2 w-full max-w-md rounded-lg border border-warning bg-warning-soft px-3 py-2 text-left"
      role="status"
    >
      <p class="min-w-0 flex-1 text-xs text-fg">
        <span class="font-medium">Speed limited to {downgrade.effective}x.</span>
        This engine cannot render at {downgrade.requested}x, so playback is running at
        {downgrade.effective}x.
      </p>
      <button
        type="button"
        class="min-h-11 min-w-11 -my-2 -mr-1 flex items-center justify-center text-fg-subtle hover:text-fg"
        aria-label="Dismiss speed notice"
        onclick={() => (dismissedKey = noticeKey)}
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  {/if}
  <div class="flex items-center justify-center gap-4">
    <button
      onclick={onRewind}
      {disabled}
      class="flex items-center gap-1 px-4 py-3 rounded-full bg-surface-sunken hover:bg-accent-soft text-fg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-40 disabled:cursor-not-allowed"
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
      class="flex items-center justify-center w-12 h-12 rounded-full bg-accent hover:bg-accent-hover text-accent-fg shadow-2 transition-colors"
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
      class="flex items-center gap-1 px-4 py-3 rounded-full bg-surface-sunken hover:bg-accent-soft text-fg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-40 disabled:cursor-not-allowed"
      aria-label={`Forward ${STEP_SENTENCES} sentences`}
    >
      {STEP_SENTENCES}
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M11.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 005 8v8a1 1 0 001.6.8l5.334-4zM19.934 12.8a1 1 0 000-1.6l-5.334-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.334-4z" />
      </svg>
    </button>
  </div>

  <div
    class="flex items-center gap-0.5 bg-surface-sunken rounded-full px-1 py-1"
    role="group"
    aria-label="Playback speed"
    aria-describedby={speedLocked ? 'speed-lock-reason' : undefined}
  >
    {#each speeds as s}
      <button
        onclick={() => onSpeedChange(s)}
        disabled={speedLocked}
        class="px-2.5 py-1 rounded-full text-xs font-semibold transition-colors {activeSpeed === s ? 'bg-accent text-accent-fg shadow-1' : 'text-fg-muted hover:bg-accent-soft'} disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
        aria-pressed={activeSpeed === s}
      >
        {s}x
      </button>
    {/each}
  </div>

  {#if speedLocked && downgrade}
    <!-- Stays after the notice is dismissed: disabled controls need a reason. -->
    <p id="speed-lock-reason" class="text-xs text-fg-subtle">
      Speed controls are unavailable — this engine only renders {downgrade.effective}x.
    </p>
  {/if}
</div>
