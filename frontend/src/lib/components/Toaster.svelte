<script lang="ts">
  import { toastStore, type ToastTone } from '$lib/stores/toast'

  /**
   * Renders whatever is on the existing `toastStore` — the store owns lifetime,
   * ordering and the three-toast cap, this only draws it. Actions (the folder
   * delete undo) are dismissed once taken so the same action cannot be run
   * twice.
   */
  const TONES: Record<ToastTone, string> = {
    info: 'border-border bg-surface-raised',
    success: 'border-success bg-success-soft',
    warning: 'border-warning bg-warning-soft',
    danger: 'border-danger bg-danger-soft',
  }

  function runAction(id: number, action: () => void) {
    action()
    toastStore.dismiss(id)
  }
</script>

<div
  class="fixed bottom-4 left-1/2 -translate-x-1/2 z-toast flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
  role="region"
  aria-label="Notifications"
>
  {#each $toastStore as toast (toast.id)}
    {@const action = toast.action}
    <div class="rounded-lg border px-4 py-3 shadow-2 {TONES[toast.tone]}" role="status">
      <div class="flex items-start gap-3">
        <div class="min-w-0 flex-1">
          <p class="text-sm font-medium text-fg">{toast.title}</p>
          {#if toast.message}
            <p class="mt-0.5 text-xs text-fg-muted">{toast.message}</p>
          {/if}
        </div>
        <button
          type="button"
          class="min-h-11 px-2 -my-2 -mr-2 text-fg-subtle hover:text-fg"
          aria-label={`Dismiss: ${toast.title}`}
          onclick={() => toastStore.dismiss(toast.id)}
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      {#if action}
        <button
          type="button"
          class="mt-2 min-h-11 text-sm font-medium text-accent underline underline-offset-2 hover:text-accent-hover"
          onclick={() => runAction(toast.id, action.onClick)}
        >
          {action.label}
        </button>
      {/if}
    </div>
  {/each}
</div>
