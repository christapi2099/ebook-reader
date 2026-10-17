<script lang="ts">
  import { onMount } from 'svelte'
  import { getVoices, setBookVoice, type Voice } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'

  /**
   * Changes the voice a book is rendered with. The current render keeps
   * playing; a new one is queued and offered through the "new version" banner.
   */
  let { bookId, currentVoice, onChanged, onError }: {
    bookId: string
    currentVoice: string | null
    onChanged?: (voice: string) => void
    onError?: (message: string) => void
  } = $props()

  let voices = $state<Voice[]>([])

  onMount(async () => {
    try { voices = await getVoices() } catch { /* the control just stays empty */ }
  })

  async function handleChange(event: Event) {
    const select = event.currentTarget as HTMLSelectElement
    const next = select.value
    if (!next || next === currentVoice) return
    const ok = confirm(
      `Re-render this book in ${next}? You can keep reading the current version while it processes.`,
    )
    if (!ok) {
      select.value = currentVoice ?? ''
      return
    }
    try {
      await setBookVoice(bookId, next)
      onChanged?.(next)
    } catch (e) {
      select.value = currentVoice ?? ''
      onError?.(toDetailMessage(e))
    }
  }
</script>

{#if voices.length > 0}
  <label class="flex items-center gap-1 text-sm text-fg-muted">
    <span class="sr-only">Book voice</span>
    <select
      class="min-h-11 rounded-md border border-border bg-surface px-2 text-sm text-fg"
      value={currentVoice ?? ''}
      onchange={handleChange}
    >
      {#each voices as v (v.id)}
        <option value={v.id}>{v.name}</option>
      {/each}
    </select>
  </label>
{/if}
