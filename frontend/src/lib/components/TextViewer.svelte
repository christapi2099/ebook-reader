<script lang="ts">
  import { bionifyText, bionifyTextToSegments, type BionicWord } from '$lib/utils/bionic-reading'

  let {
    sentences,
    currentIndex,
    currentWordIndex = -1,
    isPlaying = false,
    highlightColor = '#fef08a',
    autoscroll = true,
    bionicMode = false,
    bionicFixation = 1,
    bionicBoldRatio = 0.5,
    onSentenceClick,
  }: {
    sentences: Array<{ index: number; text: string; filtered: boolean }>
    currentIndex: number
    currentWordIndex?: number
    isPlaying?: boolean
    highlightColor?: string
    autoscroll?: boolean
    bionicMode?: boolean
    bionicFixation?: number
    bionicBoldRatio?: number
    onSentenceClick?: (index: number) => void
  } = $props()

  const currentSentence = $derived(sentences.find(s => s.index === currentIndex) || null)
  const words = $derived(currentSentence?.text.split(/\s+/) || [])

  const bionicOpts = $derived({ fixationPoint: bionicFixation, boldRatio: bionicBoldRatio })

  const currentBionicWords = $derived(
    currentSentence && bionicMode
      ? bionifyText(currentSentence.text, bionicOpts)
      : [] as BionicWord[]
  )

  const sentenceBionicSegments = $derived(
    bionicMode
      ? new Map(sentences.map(s => [s.index, bionifyTextToSegments(s.text, bionicOpts)]))
      : null
  )

  function wordTrackStyle(index: number): string {
    return index <= currentWordIndex ? 'background-color: rgba(0,0,0,0.1); font-weight: 600;' : ''
  }

  let containerRef: HTMLElement | null = null

  function handleSentenceClick(index: number) {
    onSentenceClick?.(index)
  }

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
      onclick={() => handleSentenceClick(sentence.index)}
      role="listitem"
      aria-current={sentence.index === currentIndex ? 'true' : 'false'}
    >
      {#if sentence.index === currentIndex && currentSentence && isPlaying && currentWordIndex >= 0}
        {#if bionicMode}
          {#each currentBionicWords as bw, i}
            <span
              class="transition-colors duration-100"
              style={wordTrackStyle(i)}
            >{#if bw.bold}<strong>{bw.bold}</strong>{/if}{bw.rest}{#if i < currentBionicWords.length - 1}{' '}{/if}</span>
          {/each}
        {:else}
          {#each words as word, i}
            <span
              class="transition-colors duration-100"
              style={wordTrackStyle(i)}
            >{word}{#if i < words.length - 1}{' '}{/if}</span>
          {/each}
        {/if}
      {:else if bionicMode && !sentence.filtered}
        {#each (sentenceBionicSegments?.get(sentence.index) ?? []) as seg}
          {#if seg.bold}<strong>{seg.text}</strong>{:else}{seg.text}{/if}
        {/each}
      {:else}
        {sentence.text}
      {/if}
    </p>
  {/each}
</div>
