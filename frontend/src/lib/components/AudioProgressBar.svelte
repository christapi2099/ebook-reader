<script lang="ts">
  let {
    sentences,
    currentIndex,
    elapsedSeconds = 0,
    sentenceDurations = {},
    isPlaying = false,
    buffering = false,
  }: {
    sentences: { index: number; text: string; filtered: boolean }[]
    currentIndex: number
    /**
     * Real seconds of audio played at this position, measured against the
     * AudioContext clock by `stores/audio.ts`. Never estimated.
     */
    elapsedSeconds?: number
    /**
     * Real per-sentence durations in seconds as reported by the backend
     * (`sentence_end.duration_ms`). Absent entries are simply not known yet.
     */
    sentenceDurations?: Record<number, number>
    isPlaying?: boolean
    buffering?: boolean
  } = $props()

  const playable = $derived(sentences.filter(s => !s.filtered))
  const playedCount = $derived(playable.filter(s => s.index <= currentIndex).length)

  // Position in the book, derived at render time from the sentence index — the
  // same unit `reader.ts` persists. Never a stored percentage.
  const positionPercent = $derived(
    playable.length > 0 ? (playedCount / playable.length) * 100 : 0
  )

  // A running total is only shown once the backend has reported a real duration
  // for every sentence. Until then the sum would be a partial figure, so it is
  // withheld rather than padded out with an estimate.
  const totalSeconds = $derived.by(() => {
    if (playable.length === 0) return null
    let total = 0
    for (const sentence of playable) {
      const duration = sentenceDurations[sentence.index]
      if (!(duration > 0)) return null
      total += duration
    }
    return total
  })

  function formatTime(secs: number): string {
    const s = Math.max(0, Math.floor(secs))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = s % 60
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    return `${m}:${String(sec).padStart(2, '0')}`
  }
</script>

<div class="px-3 py-1.5 flex flex-col gap-1">
  <!-- Progress track -->
  <div
    class="relative w-full h-1 bg-slate-200 rounded-full overflow-hidden"
    role="progressbar"
    aria-label="Reading position"
    aria-valuemin="0"
    aria-valuemax="100"
    aria-valuenow={Math.round(positionPercent)}
    aria-valuetext={`Sentence ${playedCount} of ${playable.length}`}
  >
    <div
      class="absolute inset-y-0 left-0 bg-blue-500 rounded-full transition-[width] duration-500"
      style="width: {positionPercent}%"
    ></div>
    {#if buffering && isPlaying}
      <!-- Shimmer overlay while buffering -->
      <div class="absolute inset-y-0 left-0 right-0 w-full h-full bg-gradient-to-r from-transparent via-white/40 to-transparent shimmer-shine"></div>
    {/if}
  </div>
  <!-- Time display: real elapsed always, real total only once it is fully known -->
  <div class="flex justify-end">
    <span class="text-xs text-slate-500 tabular-nums">
      {formatTime(elapsedSeconds)}{#if totalSeconds !== null} / {formatTime(totalSeconds)}{/if}
    </span>
  </div>
</div>
