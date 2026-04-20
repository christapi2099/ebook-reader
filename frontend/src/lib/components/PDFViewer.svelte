<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import * as pdfjsLib from 'pdfjs-dist'
  import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
  import type { Sentence } from '$lib/api'

  // Set worker once at module level — Vite resolves the ?url asset correctly
  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

  let {
    bookId,
    sentences,
    currentIndex,
    onSentenceClick,
  }: {
    bookId: string
    sentences: Sentence[]
    currentIndex: number
    onSentenceClick: (index: number) => void
  } = $props()

  let container: HTMLDivElement
  let pdfDoc: any = null
  let renderedPages: Map<number, { canvas: HTMLCanvasElement; viewport: any }> = new Map()
  let pageCount = $state(0)
  let loading = $state(true)
  let error = $state('')

  const SCALE = 1.5
  const EAGER_PAGES = 5  // render first N pages immediately, rest lazily

  let sentencesByPage = $derived(
    sentences.reduce((acc, s) => {
      if (!acc.has(s.page)) acc.set(s.page, [])
      acc.get(s.page)!.push(s)
      return acc
    }, new Map<number, Sentence[]>())
  )

  async function loadPDF() {
    loading = true
    error = ''
    try {
      const url = `http://localhost:8000/uploads/${bookId}.pdf`
      pdfDoc = await pdfjsLib.getDocument(url).promise
      pageCount = pdfDoc.numPages
      loading = false
      // Render first EAGER_PAGES immediately so user sees content fast
      for (let i = 1; i <= Math.min(EAGER_PAGES, pageCount); i++) {
        await renderPage(i)
      }
      // Render remaining pages lazily
      renderRemainingPages(EAGER_PAGES + 1)
    } catch (e: any) {
      error = e?.message ?? 'Failed to load PDF'
      loading = false
    }
  }

  async function renderRemainingPages(startPage: number) {
    for (let i = startPage; i <= pageCount; i++) {
      if (!pdfDoc) break
      await renderPage(i)
      // Yield to the event loop every 3 pages to keep UI responsive
      if (i % 3 === 0) await new Promise(r => setTimeout(r, 0))
    }
  }

  async function renderPage(pageNum: number) {
    if (!pdfDoc || renderedPages.has(pageNum - 1)) return
    const page = await pdfDoc.getPage(pageNum)
    const viewport = page.getViewport({ scale: SCALE })

    const canvas = document.createElement('canvas')
    canvas.width = viewport.width
    canvas.height = viewport.height
    const ctx = canvas.getContext('2d')!
    try {
      await page.render({ canvasContext: ctx, viewport }).promise
    } catch (e: any) {
      if (e?.name === 'RenderingCancelledException') return
      throw e
    }

    renderedPages.set(pageNum - 1, { canvas, viewport })

    const wrapper = document.createElement('div')
    wrapper.className = 'relative mb-4'
    wrapper.style.width = viewport.width + 'px'
    wrapper.style.height = viewport.height + 'px'
    wrapper.dataset.page = String(pageNum - 1)

    canvas.className = 'block'
    wrapper.appendChild(canvas)

    const overlay = document.createElement('div')
    overlay.className = 'absolute inset-0 pointer-events-auto'
    overlay.dataset.overlay = String(pageNum - 1)
    wrapper.appendChild(overlay)

    container?.appendChild(wrapper)
    drawHighlights(pageNum - 1)
  }

  function drawHighlights(page: number) {
    const pageInfo = renderedPages.get(page)
    if (!pageInfo) return
    const overlay = container?.querySelector(`[data-overlay="${page}"]`) as HTMLElement
    if (!overlay) return

    overlay.innerHTML = ''
    const pageSentences = sentencesByPage.get(page) ?? []
    for (const s of pageSentences) {
      if (s.filtered) continue

      // PyMuPDF coords have origin top-left, y increases downward — same as canvas.
      // Direct multiply by SCALE is the correct mapping (no y-axis flip needed).
      const PAD = 2
      const left   = s.x0 * SCALE - PAD
      const top    = s.y0 * SCALE - PAD
      const width  = (s.x1 - s.x0) * SCALE + PAD * 2
      const height = (s.y1 - s.y0) * SCALE + PAD * 2

      const div = document.createElement('div')
      div.className = [
        'absolute cursor-pointer transition-colors',
        s.index === currentIndex ? 'bg-yellow-200/60' : 'hover:bg-blue-100/40',
      ].join(' ')
      div.style.left   = left + 'px'
      div.style.top    = top + 'px'
      div.style.width  = width + 'px'
      div.style.height = height + 'px'
      div.title = s.text
      div.dataset.index = String(s.index)
      div.addEventListener('click', () => onSentenceClick(s.index))
      overlay.appendChild(div)
    }
  }

  $effect(() => {
    void currentIndex
    for (const page of renderedPages.keys()) {
      drawHighlights(page)
    }
  })

  $effect(() => {
    const s = sentences.find(s => s.index === currentIndex)
    if (!s) return
    const pageInfo = renderedPages.get(s.page)
    if (!pageInfo) return
    const [, y] = pdfToCanvas(s.x0, s.y0, pageInfo.viewport)
    const wrapper = container?.querySelector(`[data-page="${s.page}"]`) as HTMLElement
    if (!wrapper) return
    container.scrollTo({ top: wrapper.offsetTop + y - 200, behavior: 'smooth' })
  })

  onMount(() => { loadPDF() })
  onDestroy(() => { pdfDoc?.destroy() })
</script>

<div class="relative h-full w-full overflow-auto bg-slate-100">
  {#if loading}
    <div class="flex h-full items-center justify-center gap-2 text-slate-500">
      <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading PDF…
    </div>
  {:else if error}
    <div class="flex h-full items-center justify-center text-red-500">{error}</div>
  {/if}
  <div bind:this={container} class="mx-auto flex flex-col items-center py-6"></div>
</div>
