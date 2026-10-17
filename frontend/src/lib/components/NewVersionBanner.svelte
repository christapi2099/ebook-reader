<script lang="ts">
  /** A newer render of the open book is ready. Switching reloads the reader. */
  let { voice, onSwitch, onDismiss }: {
    voice: string | null
    onSwitch: () => void
    onDismiss: () => void
  } = $props()
</script>

<div class="flex items-center justify-between gap-3 border-b border-accent bg-accent-soft px-3 py-2 text-sm text-fg" role="status" data-new-version-banner>
  <span>New version ready{voice ? ` (voice ${voice})` : ''}</span>
  <span class="flex items-center gap-2">
    <button type="button" class="min-h-11 rounded-md bg-accent px-3 font-semibold text-accent-fg" onclick={onSwitch}>Switch</button>
    <button type="button" class="min-h-11 rounded-md px-3 text-fg-muted hover:bg-surface-sunken" onclick={onDismiss}>Later</button>
  </span>
</div>
