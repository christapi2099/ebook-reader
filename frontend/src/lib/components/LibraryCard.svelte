<script lang="ts">
  let { book, onClick }: {
    book: { id: string; title: string; author: string | null; file_type: string; page_count: number }
    onClick: (id: string) => void
  } = $props()
</script>

<div
  class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 cursor-pointer hover:shadow-md transition-shadow"
  onclick={() => onClick(book.id)}
  role="button"
  tabindex="0"
  onkeydown={(e) => e.key === 'Enter' && onClick(book.id)}
>
  <div class="h-32 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 relative">
    <span class="absolute top-2 right-2 text-xs bg-blue-100 text-blue-600 rounded px-1.5 py-0.5 font-medium uppercase">
      {book.file_type}
    </span>
  </div>
  <h3 class="font-semibold text-slate-800 mt-2 line-clamp-2 text-sm">{book.title}</h3>
  <p class="text-sm text-slate-500 mt-1">{book.author ?? 'Unknown'}</p>
  <p class="text-xs text-slate-400 mt-1">{book.page_count} pages</p>
</div>
