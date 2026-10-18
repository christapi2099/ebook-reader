<script lang="ts">
  /**
   * Determinate progress bar. `percent` is 0-100; `null` shows an indeterminate
   * shimmer for work whose size is not known yet (e.g. a queued book).
   * The fill animates with transform only (DESIGN.md §1.6) and stops moving
   * under prefers-reduced-motion.
   */
  let {
    percent = null,
    label,
    size = 'md',
  }: {
    percent?: number | null
    label: string
    size?: 'sm' | 'md'
  } = $props()

  const clamped = $derived(percent === null ? null : Math.min(100, Math.max(0, Math.round(percent))))
  const scale = $derived(clamped === null ? 0 : clamped / 100)
</script>

<div
  class="overflow-hidden rounded-full bg-border {size === 'md' ? 'h-2.5' : 'h-1.5'}"
  role="progressbar"
  aria-label={label}
  aria-valuemin="0"
  aria-valuemax="100"
  aria-valuenow={clamped ?? undefined}
  aria-valuetext={clamped === null ? 'waiting' : `${clamped}%`}
  data-progress-bar
>
  {#if clamped === null}
    <div class="h-full w-1/3 rounded-full bg-accent motion-safe:animate-pulse"></div>
  {:else}
    <div
      class="h-full w-full origin-left rounded-full bg-accent motion-safe:transition-transform motion-safe:duration-[320ms] motion-safe:ease-out"
      style={`transform: scaleX(${scale})`}
      data-progress-fill
    ></div>
  {/if}
</div>
