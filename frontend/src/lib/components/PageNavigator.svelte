<script lang="ts">
  let {
    currentPage = 0,
    totalPages = 1,
    onGoToPage,
  }: {
    currentPage?: number
    totalPages?: number
    onGoToPage: (page: number) => void
  } = $props()

  let draftValue = $state(String(currentPage + 1))

  $effect(() => { draftValue = String(currentPage + 1) })

  function handleInput(e: Event) {
    const v = parseInt((e.target as HTMLInputElement).value, 10)
    if (!isNaN(v)) onGoToPage(Math.max(0, Math.min(v - 1, totalPages - 1)))
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') handleInput(e)
  }
</script>

<div class="flex items-center gap-1 text-sm text-slate-700">
  <button
    onclick={() => onGoToPage(Math.max(0, currentPage - 1))}
    disabled={currentPage === 0}
    class="p-1 rounded hover:bg-slate-100 disabled:opacity-40"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
    </svg>
  </button>
  <input
    type="number"
    bind:value={draftValue}
    onblur={handleInput}
    onkeydown={handleKeydown}
    onclick={(e) => (e.target as HTMLInputElement).select()}
    class="w-14 px-1 py-0.5 border border-slate-300 rounded text-center focus:outline-none focus:ring-1 focus:ring-blue-500"
    min="1"
    max={totalPages}
  />
  <span class="text-slate-500">/ {totalPages}</span>
  <button
    onclick={() => onGoToPage(Math.min(totalPages - 1, currentPage + 1))}
    disabled={currentPage === totalPages - 1}
    class="p-1 rounded hover:bg-slate-100 disabled:opacity-40"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
    </svg>
  </button>
</div>
