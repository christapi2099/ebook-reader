<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import type { Sentence } from '$lib/api'

  let {
    bookId,
    filePath,
    sentences,
    currentIndex,
    onSentenceClick,
  }: {
    bookId: string
    filePath: string
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

  // Group sentences by page for efficient lookup
  let sentencesByPage = $derived(
    sentences.reduce((acc, s) => {
      if (!acc.has(s.page)) acc.set(s.page, [])
      acc.get(s.page)!.push(s)
      return acc
    }, new Map<number, Sentence[]>())
  )

  // PDF.js scale factor
  const SCALE = 1.5

  async function loadPDF() {
    if (!filePath) return
    loading = true
    error = ''
    try {
      // @ts-ignore
      const pdfjsLib = await import('pdfjs-dist')
      // Point worker to the bundled worker
      pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
        'pdfjs-dist/build/pdf.worker.min.mjs',
        import.meta.url
      ).toString()

      // Fetch the PDF bytes from the backend uploads directory
      const url = `http://localhost:8000/uploads/${bookId}.pdf`
      pdfDoc = await pdfjsLib.getDocument(url).promise
      pageCount = pdfDoc.numPages
      loading = false
      await renderAllPages()
    } catch (e: any) {
      error = e?.message ?? 'Failed to load PDF'
      loading = false
    }
  }

  async function renderAllPages() {
    if (!pdfDoc) return
    for (let pageNum = 1; pageNum <= pdfDoc.numPages; pageNum++) {
      await renderPage(pageNum)
    }
  }

  async function renderPage(pageNum: number) {
    const page = await pdfDoc.getPage(pageNum)
    const viewport = page.getViewport({ scale: SCALE })

    const canvas = document.createElement('canvas')
    canvas.width = viewport.width
    canvas.height = viewport.height

    const ctx = canvas.getContext('2d')!
    await page.render({ canvasContext: ctx, viewport }).promise

    renderedPages.set(pageNum - 1, { canvas, viewport })

    // Build page wrapper with canvas + highlight overlay
    const wrapper = document.createElement('div')
    wrapper.className = 'relative mb-4'
    wrapper.style.width = viewport.width + 'px'
    wrapper.style.height = viewport.height + 'px'
    wrapper.dataset.page = String(pageNum - 1)

    canvas.className = 'block'
    wrapper.appendChild(canvas)

    // Highlight overlay
    const overlay = document.createElement('div')
    overlay.className = 'absolute inset-0 pointer-events-auto'
    overlay.dataset.overlay = String(pageNum - 1)
    wrapper.appendChild(overlay)

    container.appendChild(wrapper)
    drawHighlights(pageNum - 1)
  }

  function pdfToCanvas(
    pdfX: number,
    pdfY: number,
    viewport: any
  ): [number, number] {
    // PDF coords: origin bottom-left. Canvas: origin top-left.
    const [cx, cy] = viewport.convertToViewportPoint(pdfX, pdfY)
    return [cx, cy]
  }

  function drawHighlights(page: number) {
    const pageInfo = renderedPages.get(page)
    if (!pageInfo) return

    const overlay = container.querySelector(`[data-overlay="${page}"]`) as HTMLElement
    if (!overlay) return

    overlay.innerHTML = ''

    const pageSentences = sentencesByPage.get(page) ?? []
    for (const s of pageSentences) {
      if (s.filtered) continue

      const [x0, y0c] = pdfToCanvas(s.x0, s.y1, pageInfo.viewport)
      const [x1, y1c] = pdfToCanvas(s.x1, s.y0, pageInfo.viewport)

      const div = document.createElement('div')
      div.className = [
        'absolute cursor-pointer transition-colors',
        s.index === currentIndex
          ? 'bg-yellow-200/60'
          : 'hover:bg-blue-100/40',
      ].join(' ')
      div.style.left = x0 + 'px'
      div.style.top = y0c + 'px'
      div.style.width = Math.abs(x1 - x0) + 'px'
      div.style.height = Math.abs(y1c - y0c) + 'px'
      div.dataset.index = String(s.index)

      div.addEventListener('click', () => onSentenceClick(s.index))
      overlay.appendChild(div)
    }
  }

  // Redraw highlights on every page when currentIndex changes
  $effect(() => {
    // reactive on currentIndex
    void currentIndex
    for (const page of renderedPages.keys()) {
      drawHighlights(page)
    }
  })

  // Scroll current sentence into view
  $effect(() => {
    const s = sentences.find(s => s.index === currentIndex)
    if (!s) return
    const pageInfo = renderedPages.get(s.page)
    if (!pageInfo) return
    const [, y] = pdfToCanvas(s.x0, s.y0, pageInfo.viewport)
    const wrapper = container?.querySelector(`[data-page="${s.page}"]`) as HTMLElement
    if (!wrapper) return
    const absoluteY = wrapper.offsetTop + y
    container.scrollTo({ top: absoluteY - 200, behavior: 'smooth' })
  })

  onMount(() => {
    loadPDF()
  })

  onDestroy(() => {
    pdfDoc?.destroy()
  })
</script>

<div class="relative h-full w-full overflow-auto bg-slate-100">
  {#if loading}
    <div class="flex h-full items-center justify-center text-slate-500">
      Loading PDF…
    </div>
  {:else if error}
    <div class="flex h-full items-center justify-center text-red-500">
      {error}
    </div>
  {/if}
  <div
    bind:this={container}
    class="mx-auto flex flex-col items-center py-6"
  ></div>
</div>
