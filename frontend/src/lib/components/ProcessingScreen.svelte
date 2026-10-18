<script lang="ts">
  import type { Book } from '$lib/api'
  import { formatEta } from '$lib/utils/render-status'
  import ProgressBar from '$lib/components/ui/ProgressBar.svelte'

  /**
   * Shown instead of the reader while a book has no ready render. The reader
   * route polls and swaps this out the moment a generation is published.
   */
  let { book, failed = false, onRetry, onBack }: {
    book: Book | null
    failed?: boolean
    onRetry?: () => void
    onBack?: () => void
  } = $props()

  const percent = $derived(Math.round(Math.min(1, Math.max(0, book?.progress ?? 0)) * 100))
  const eta = $derived(formatEta(book?.eta_seconds))
  const queued = $derived(book?.job_status === 'pending')
</script>

<div class="flex h-full flex-col items-center justify-center gap-3 p-6 text-center" data-processing-screen>
  {#if failed}
    <p class="text-lg font-medium text-danger">Processing failed</p>
    <p class="text-sm text-fg-muted">This book could not be prepared for reading.</p>
    {#if onRetry}
      <button type="button" class="min-h-11 rounded-md border border-border px-4 text-sm font-semibold text-accent hover:bg-accent-soft" onclick={onRetry}>
        Retry
      </button>
    {/if}
  {:else}
    <p class="text-lg font-medium text-fg" role="status">
      {queued ? 'Waiting in the queue…' : 'Preparing your audiobook…'}
    </p>
    <div class="w-72 max-w-full">
      <ProgressBar percent={queued ? null : percent} label="Processing progress" />
    </div>
    <p class="text-sm font-medium tabular-nums text-fg">
      {#if queued && book?.queue_position != null}#{book.queue_position} in queue{:else if queued}Queued{:else}{percent}%{/if}
      {#if eta} · {eta} left{/if}
    </p>
    <p class="text-xs text-fg-subtle">This page opens automatically when it is ready.</p>
  {/if}
  {#if onBack}
    <button type="button" class="min-h-11 rounded-md px-3 text-sm text-fg-muted hover:bg-surface-sunken" onclick={onBack}>
      Back to library
    </button>
  {/if}
</div>
