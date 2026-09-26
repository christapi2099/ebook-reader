<script lang="ts">
  import type { Sentence } from '$lib/api'
  import { overlayLayer } from '$lib/actions/overlay-layer'

  let {
    sentences,
    currentMatch: initialMatch = 0,
    onClose,
    onResults,
  }: {
    sentences: Sentence[]
    currentMatch?: number
    onClose: () => void
    onResults: (matches: Sentence[], currentMatch: number) => void
  } = $props()

  let query = $state('')
  let matches = $state<Sentence[]>([])
  let currentMatch = $state(0)

  $effect(() => {
    currentMatch = initialMatch
  })

  function search() {
    const q = query.toLowerCase().trim()
    if (!q) {
      matches = []
      currentMatch = 0
      onResults([], -1)
      return
    }
    matches = sentences.filter(s => !s.filtered && s.text.toLowerCase().includes(q))
    currentMatch = matches.length > 0 ? 0 : -1
    onResults(matches, currentMatch >= 0 ? matches[currentMatch].index : -1)
  }

  function handleInput() {
    search()
  }

  function goPrev() {
    if (matches.length === 0) return
    currentMatch = (currentMatch - 1 + matches.length) % matches.length
    onResults(matches, matches[currentMatch].index)
  }

  function goNext() {
    if (matches.length === 0) return
    currentMatch = (currentMatch + 1) % matches.length
    onResults(matches, matches[currentMatch].index)
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      if (e.shiftKey) goPrev()
      else goNext()
    }
    // Escape is handled by the overlay stack, which routes it to the topmost layer.
  }
</script>

<div
  class="bg-surface border-b border-border shadow-1"
  role="search"
  use:overlayLayer={{ onClose, modal: false, initialFocus: 'input' }}
>
  <div class="flex items-center gap-2 px-3 py-2">
    <svg class="w-4 h-4 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="8" stroke-width="2" />
      <path stroke-linecap="round" stroke-width="2" d="m21 21-4.3-4.3" />
    </svg>
    <input
      type="text"
      placeholder="Search in book…"
      aria-label="Search in book"
      bind:value={query}
      oninput={handleInput}
      onkeydown={handleKeydown}
      class="flex-1 px-2 py-1.5 text-sm border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-focus-ring"
    />
    {#if matches.length > 0}
      <span class="text-xs text-fg-muted tabular-nums whitespace-nowrap">
        {currentMatch + 1} / {matches.length}
      </span>
      <button
        class="p-1 rounded hover:bg-surface-sunken text-fg-muted disabled:opacity-30 transition-colors"
        disabled={matches.length <= 1}
        onclick={goPrev}
        aria-label="Previous match"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
      </button>
      <button
        class="p-1 rounded hover:bg-surface-sunken text-fg-muted disabled:opacity-30 transition-colors"
        disabled={matches.length <= 1}
        onclick={goNext}
        aria-label="Next match"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>
    {/if}
    <button
      class="p-1 rounded hover:bg-surface-sunken text-fg-subtle transition-colors"
      onclick={onClose}
      aria-label="Close search"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>
  </div>
</div>
