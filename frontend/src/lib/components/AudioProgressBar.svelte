<script lang="ts">
  import { onDestroy } from 'svelte'

  let {
    sentences,
    currentIndex,
    isPlaying,
    speed,
  }: {
    sentences: { index: number; text: string; filtered: boolean }[]
    currentIndex: number
    isPlaying: boolean
    speed: number
  } = $props()

  // Average English TTS: ~150 wpm at 1x speed
  const WPM = 150

  function sentenceDuration(text: string, spd: number): number {
    const words = text.trim().split(/\s+/).length
    return Math.max(0.5, (words / WPM) * 60 / spd)
  }

  let totalSeconds = $derived(
    sentences
      .filter(s => !s.filtered)
      .reduce((acc, s) => acc + sentenceDuration(s.text, speed), 0)
  )

  let elapsedSeconds = $derived(
    sentences
      .filter(s => !s.filtered && s.index <= currentIndex)
      .reduce((acc, s) => acc + sentenceDuration(s.text, speed), 0)
  )

  // Fine-grained real-time offset ticked forward while playing
  let fineOffset = $state(0)
  let intervalId: ReturnType<typeof setInterval> | null = null

  $effect(() => {
    currentIndex
    isPlaying
    fineOffset = 0
  })

  $effect(() => {
    if (intervalId) { clearInterval(intervalId); intervalId = null }
    if (isPlaying) {
      intervalId = setInterval(() => {
        fineOffset += 0.5 / speed
      }, 500)
    }
    return () => {
      if (intervalId) { clearInterval(intervalId); intervalId = null }
    }
  })

  onDestroy(() => {
    if (intervalId) clearInterval(intervalId)
  })

  function formatTime(secs: number): string {
    const s = Math.floor(secs)
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = s % 60
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  let displayElapsed = $derived(Math.min(elapsedSeconds + fineOffset, totalSeconds))
  let progress = $derived(totalSeconds > 0 ? (displayElapsed / totalSeconds) * 100 : 0)
</script>

<div class="px-3 py-1.5 flex flex-col gap-1">
  <!-- Progress track -->
  <div class="relative w-full h-1 bg-slate-200 rounded-full overflow-hidden">
    <div
      class="absolute inset-y-0 left-0 bg-blue-500 rounded-full transition-[width] duration-500"
      style="width: {progress}%"
    ></div>
  </div>
  <!-- Time display -->
  <div class="flex justify-end">
    <span class="text-xs text-slate-500 tabular-nums">
      {formatTime(displayElapsed)} / {formatTime(totalSeconds)}
    </span>
  </div>
</div>
