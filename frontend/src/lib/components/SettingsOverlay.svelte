<script lang="ts">
  import { settingsStore, type SettingsState } from '$lib/stores/settings'
  import { get } from 'svelte/store'

  let { onClose }: { onClose: () => void } = $props()

  let settings: SettingsState = $state({ ...get(settingsStore) })

  const colorSwatches = [
    { value: '#fef08a', label: 'Yellow' },
    { value: '#86efac', label: 'Green' },
    { value: '#93c5fd', label: 'Blue' },
    { value: '#fdba74', label: 'Orange' },
    { value: '#f9a8d4', label: 'Pink' },
    { value: '#c4b5fd', label: 'Purple' },
    { value: '#fca5a5', label: 'Red' },
    { value: '#67e8f9', label: 'Cyan' },
  ]

  function setColor(color: string) {
    settings.highlightColor = color
    settingsStore.setHighlightColor(color)
  }

  function toggleAutoscroll() {
    settings.autoscroll = !settings.autoscroll
    settingsStore.toggleAutoscroll()
  }

  function toggleHotkeys() {
    settings.hotkeysEnabled = !settings.hotkeysEnabled
    settingsStore.toggleHotkeys()
  }
</script>

  <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" role="button" tabindex="-1" onclick={onClose} onkeydown={(e) => { if (e.key === 'Escape') onClose() }}>
    <div class="bg-white rounded-xl p-6 max-w-sm w-full shadow-xl mx-4" onclick={(e) => e.stopPropagation()}>
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-bold text-slate-800">Settings</h2>
      <button class="text-slate-400 hover:text-slate-700 transition-colors" onclick={onClose}>
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <div class="space-y-5">
      <div>
        <label id="color-label" class="block text-sm font-medium text-slate-700 mb-2">Highlight Color</label>
        <div class="flex flex-wrap gap-2" role="radiogroup" aria-labelledby="color-label">
          {#each colorSwatches as swatch}
            <button
              class="w-8 h-8 rounded-full border-2 transition-all {settings.highlightColor === swatch.value ? 'border-slate-800 scale-110' : 'border-transparent hover:scale-105'}"
              style="background-color: {swatch.value}"
              title={swatch.label}
              aria-checked={settings.highlightColor === swatch.value}
              onclick={() => setColor(swatch.value)}
              role="radio"
            ></button>
          {/each}
        </div>
      </div>

      <div>
        <div class="flex items-center justify-between">
          <div>
            <span class="text-sm font-medium text-slate-700">Auto-Scroll</span>
            <p class="text-xs text-slate-500">Follow current sentence automatically</p>
          </div>
          <button
            role="switch"
            aria-checked={settings.autoscroll}
            class="relative w-10 h-5 rounded-full transition-colors {settings.autoscroll ? 'bg-blue-500' : 'bg-slate-300'}"
            onclick={toggleAutoscroll}
          >
            <div class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform {settings.autoscroll ? 'translate-x-5' : ''}"></div>
          </button>
        </div>
      </div>

      <div>
        <div class="flex items-center justify-between">
          <div>
            <span class="text-sm font-medium text-slate-700">Keyboard Hotkeys</span>
            <p class="text-xs text-slate-500">Space, arrows, B, F, Esc</p>
          </div>
          <button
            role="switch"
            aria-checked={settings.hotkeysEnabled}
            class="relative w-10 h-5 rounded-full transition-colors {settings.hotkeysEnabled ? 'bg-blue-500' : 'bg-slate-300'}"
            onclick={toggleHotkeys}
          >
            <div class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform {settings.hotkeysEnabled ? 'translate-x-5' : ''}"></div>
          </button>
        </div>
      </div>

      <div class="bg-slate-50 rounded-lg p-3 text-xs text-slate-500 space-y-1">
        <p class="font-medium text-slate-700 mb-1">Hotkey Reference</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">Space</kbd> Play / Pause</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">←</kbd> <kbd class="px-1 bg-slate-200 rounded text-xs">→</kbd> Previous / Next sentence</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">↑</kbd> <kbd class="px-1 bg-slate-200 rounded text-xs">↓</kbd> Speed up / Slow down</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">B</kbd> Bookmark current position</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">F</kbd> Open search</p>
        <p><kbd class="px-1 bg-slate-200 rounded text-xs">Esc</kbd> Close overlays</p>
      </div>
    </div>
  </div>
</div>
