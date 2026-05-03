<script lang="ts">
  let {
    sentences,
    currentIndex,
    currentWordIndex = -1,
    isPlaying = false,
    highlightColor = '#fef08a',
    autoscroll = true,
    onSentenceClick,
  }: {
    sentences: Array<{ index: number; text: string; filtered: boolean }>
    currentIndex: number
    currentWordIndex?: number
    isPlaying?: boolean
    highlightColor?: string
    autoscroll?: boolean
    onSentenceClick?: (index: number) => void
  } = $props()

  const currentSentence = $derived(sentences.find(s => s.index === currentIndex) || null)
  const words = $derived(currentSentence?.text.split(/\s+/) || [])

  let containerRef: HTMLElement | null = null

  $effect(() => {
    if (autoscroll && containerRef && currentSentence) {
      const sentenceEl = containerRef.querySelector(`[data-sentence-index="${currentIndex}"]`) as HTMLElement
      if (sentenceEl) {
        sentenceEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  })
</script>

<div
  class="overflow-y-auto flex-1 p-4 space-y-4"
  bind:this={containerRef}
  role="list"
  aria-label="Sentences"
>
  {#each sentences as sentence (sentence.index)}
    <p
      class="cursor-pointer rounded-lg p-2 transition-colors"
      data-sentence-index={sentence.index}
      data-highlighted={sentence.index === currentIndex}
      style={sentence.index === currentIndex ? `background-color: ${highlightColor}` : ''}
      onclick={() => handleClick(sentence.index)}
      role="listitem"
      aria-current={sentence.index === currentIndex ? 'true' : 'false'}
    >
      {#if sentence.index === currentIndex && currentSentence && isPlaying && currentWordIndex >= 0}
        {#each words as word, i}
          <span
            class="transition-colors duration-100"
            style={i <= currentWordIndex ? 'background-color: rgba(0,0,0,0.1); font-weight: 600;' : ''}
          >
            {word}{#if i < words.length - 1}{' '}{/if}
          </span>
        {/each}
      {:else}
        {sentence.text}
      {/if}
    </p>
  {/each}
</div>
