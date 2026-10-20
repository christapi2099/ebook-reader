<script lang="ts">
  // Stands in for PDFViewer: counts how often a derived over `sentences` re-runs,
  // the way PDFViewer's sentencesByPage (and its overlay rebuild) does.
  let { sentences }: { sentences: unknown[]; [key: string]: unknown } = $props()
  const probe = (globalThis as unknown as { __probe: { recomputes: number; last: unknown } }).__probe
  const count = $derived.by(() => { probe.recomputes++; return sentences.length })
  $effect(() => { void count; probe.last = sentences })
</script>
<div data-probe={count}></div>
