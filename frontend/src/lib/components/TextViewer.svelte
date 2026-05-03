<script lang="ts">
  let {
    sentences,
    currentIndex,
    isPlaying = false,
    highlightColor = '#fef08a',
    autoscroll = true,
    onSentenceClick,
  }: {
    sentences: Array<{ index: number; text: string; filtered: boolean }>
    currentIndex: number
    isPlaying?: boolean
    highlightColor?: string
    autoscroll?: boolean
    onSentenceClick?: (index: number) => void
  } = $props()

  let wordIndex = $state(0)
  let rafId: number | null = null

  const currentSentence = $derived(sentences.find(s => s.index === currentIndex) || null)
  const words = $derived(currentSentence?.text.split(/\s+/) || [])

  $effect(() => {
    currentIndex
    wordIndex = 0
  })

  $effect(() => {
    if (!currentSentence || !isPlaying) {
      if (rafId) {
        cancelAnimationFrame(rafId)
        rafId = null
      }
      return
    }

    const wordCount = words.length
    if (wordCount === 0) return

    const baseDurationMs = (wordCount / 150) * 60 * 1000
    const estimatedWordDuration = baseDurationMs / wordCount

    let startTime: number | null = null

    const tick = (timestamp: number) => {
      if (startTime === null) startTime = timestamp
      const elapsed = timestamp - startTime

      const newWordIndex = Math.min(
        Math.floor(elapsed / estimatedWordDuration),
        wordCount - 1
      )

      if (newWordIndex !== wordIndex) {
        wordIndex = newWordIndex
      }

      rafId = requestAnimationFrame(tick)
    }

    rafId = requestAnimationFrame(tick)

    return () => {
      if (rafId) {
        cancelAnimationFrame(rafId)
        rafId = null
      }
    }
  })

  function handleClick(index: number) {
    if (onSentenceClick) {
      onSentenceClick(index)
      wordIndex = 0
    }
  }

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
      {#if sentence.index === currentIndex && currentSentence && isPlaying}
        {#each words as word, i}
          <span
            class="transition-colors duration-100"
            style={i <= wordIndex ? 'background-color: rgba(0,0,0,0.1); font-weight: 600;' : ''}
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
