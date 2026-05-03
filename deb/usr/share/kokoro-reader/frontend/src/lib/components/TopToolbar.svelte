<script lang="ts">
  let { onToggleCC, onCopyText, onSave, onSearch, onSettings }: {
    onToggleCC: () => void
    onCopyText: () => void
    onSave: () => void
    onSearch: () => void
    onSettings: () => void
  } = $props()
</script>

<div class="flex flex-row gap-1">
  <button
    class="p-3 rounded-md hover:bg-slate-100 text-slate-600 border border-slate-300 text-xs font-bold min-w-[44px] min-h-[44px]"
    onclick={onToggleCC}
    aria-label="Toggle closed captions"
  >
    CC
  </button>

  <button class="p-3 rounded-md hover:bg-slate-100 text-slate-600" onclick={onCopyText} aria-label="Copy text">
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </svg>
  </button>

  <button class="p-3 rounded-md hover:bg-slate-100 text-slate-600" onclick={onSave} aria-label="Save bookmark">
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" />
    </svg>
  </button>

  <button class="p-3 rounded-md hover:bg-slate-100 text-slate-600" onclick={onSearch} aria-label="Search">
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  </button>

  <button class="p-3 rounded-md hover:bg-slate-100 text-slate-600" onclick={onSettings} aria-label="Settings">
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="5" r="1" fill="currentColor" />
      <circle cx="12" cy="12" r="1" fill="currentColor" />
      <circle cx="12" cy="19" r="1" fill="currentColor" />
    </svg>
  </button>
</div>
