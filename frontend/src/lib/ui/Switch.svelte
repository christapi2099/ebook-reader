<script lang="ts">
  /**
   * A labelled on/off control.
   *
   * Lifted from the `switchRow` snippet in SettingsOverlay, which was already a
   * component in everything but name. The reason to move it is the ARIA wiring:
   * an earlier bug gave the switch no accessible name, and the fix — pointing
   * `aria-labelledby` and `aria-describedby` at ids derived from one `id` prop —
   * is the kind of thing that must be written once rather than repeated by hand
   * at each new toggle. The 44px hit area around a 24px track lives here too.
   */
  let {
    id,
    label,
    hint,
    checked,
    onToggle,
  }: {
    /** Also the prefix for the label and hint element ids. */
    id: string
    label: string
    hint?: string
    checked: boolean
    onToggle: () => void
  } = $props()
</script>

<div class="flex items-center justify-between gap-3">
  <div class="min-w-0">
    <span id="{id}-label" class="text-sm font-medium text-fg">{label}</span>
    {#if hint}
      <p id="{id}-hint" class="text-xs text-fg-muted">{hint}</p>
    {/if}
  </div>
  <button
    type="button"
    role="switch"
    {id}
    aria-checked={checked}
    aria-labelledby="{id}-label"
    aria-describedby={hint ? `${id}-hint` : undefined}
    class="flex h-11 w-11 shrink-0 items-center justify-center"
    onclick={onToggle}
  >
    <span
      class="relative block h-6 w-11 rounded-full transition-colors {checked
        ? 'bg-accent'
        : 'bg-border-strong'}"
    >
      <span
        class="absolute left-0.5 top-0.5 block h-5 w-5 rounded-full bg-surface shadow transition-transform {checked
          ? 'translate-x-5'
          : ''}"
      ></span>
    </span>
  </button>
</div>
