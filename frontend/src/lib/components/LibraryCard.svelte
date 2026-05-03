<script lang="ts">
  let { book, onClick, onDelete }: {
    book: { id: string; title: string; author: string | null; file_type: string; page_count: number }
    onClick: (id: string) => void
    onDelete?: (id: string) => void
  } = $props()

  let showMenu = $state(false)
</script>

<div
  class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 hover:shadow-md transition-shadow relative group cursor-pointer"
  role="button"
  tabindex="0"
  aria-label={`${book.title} by ${book.author ?? 'Unknown'}`}
  onclick={() => onClick(book.id)}
  onkeydown={(e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onClick(book.id)
    }
  }}
>
  {#if onDelete}
    <div class="absolute top-2 right-2 z-10" role="button" tabindex="-1" onclick={(e) => e.stopPropagation()}>
      <button
        class="opacity-0 group-hover:opacity-100 w-6 h-6 rounded-full bg-slate-100 hover:bg-red-100 text-slate-500 hover:text-red-600 flex items-center justify-center transition-all"
        onclick={() => (showMenu = !showMenu)}
        aria-label="Book options"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      </button>
      {#if showMenu}
        <div class="relative">
          <div class="fixed inset-0 z-10" role="button" tabindex="-1" onclick={() => (showMenu = false)}></div>
          <div class="absolute right-0 top-8 bg-white border border-slate-200 rounded-lg shadow-lg z-20 py-1 min-w-[120px]">
            <button
              class="w-full px-3 py-1.5 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
              onclick={() => { onDelete(book.id); showMenu = false }}
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete
            </button>
          </div>
        </div>
      {/if}
    </div>
  {/if}
  <div class="h-32 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 relative">
    <span class="absolute top-2 right-2 text-xs bg-blue-100 text-blue-600 rounded px-1.5 py-0.5 font-medium uppercase">
      {book.file_type}
    </span>
  </div>
  <h3 class="font-semibold text-slate-800 mt-2 line-clamp-2 text-sm">{book.title}</h3>
  <p class="text-sm text-slate-500 mt-1">{book.author ?? 'Unknown'}</p>
  <p class="text-xs text-slate-400 mt-1">{book.page_count} pages</p>
</div>
