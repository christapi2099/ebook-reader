<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import * as pdfjsLib from 'pdfjs-dist'
  import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
  import type { Sentence } from '$lib/api'

  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

  let {
    bookId,
    sentences,
    currentIndex,
    onSentenceClick,
    onPageChange,
    buffering = false,
    pageToScroll = null,
  }: {
    bookId: string
    sentences: Sentence[]
    currentIndex: number
    onSentenceClick: (index: number) => void
    onPageChange?: (page: number) => void
    buffering?: boolean
    pageToScroll?: number | null
  } = $props()

  let sentenceElements = new Map<number, HTMLDivElement>()
  let scrollEl: HTMLDivElement   // outer scroll container (used for scrollTo)
  let pagesEl: HTMLDivElement    // inner pages container (pages appended here)
  let pdfDoc: any = null
  let renderedPages: Map<number, { canvas: HTMLCanvasElement; viewport: any }> = new Map()
  let pageCount = $state(0)
  let loading = $state(true)
  let error = $state('')
  let scrollDebounce: ReturnType<typeof setTimeout> | null = null
  let prevHighlightIndex = -1
  let intersectionObserver: IntersectionObserver | null = null
  let resizeObserver: ResizeObserver | null = null

  const MAX_WIDTH_PX = 900 // Max width for pages on ultrawide screens
  const BASE_SCALE = 1.5   // Original scale for A4 portrait
  const SCALE_CHANGE_THRESHOLD = 0.05 // Re-render if scale changes by this much

  let containerWidth = $state(0)
  let effectiveScale = $state(BASE_SCALE)

  let sentencesByPage = $derived(
    sentences.reduce((acc, s) => {
      if (!acc.has(s.page)) acc.set(s.page, [])
      acc.get(s.page)!.push(s)
      return acc
    }, new Map<number, Sentence[]>())
  )

  $effect(() => {
    if (pdfDoc && containerWidth > 0) {
      // Get natural page width from the first page's viewport with BASE_SCALE
      const firstPageViewport = renderedPages.get(0)?.viewport || pdfDoc.getPage(1).then((p: any) => p.getViewport({ scale: BASE_SCALE }))
      Promise.resolve(firstPageViewport).then(viewport => {
        const naturalPageWidth = viewport.width
        let newScale = BASE_SCALE

        if (containerWidth < naturalPageWidth) {
          // Scale down to fit narrow screens
          newScale = containerWidth / naturalPageWidth * BASE_SCALE
        } else if (containerWidth > MAX_WIDTH_PX) {
          // Cap width on ultrawide screens
          newScale = MAX_WIDTH_PX / naturalPageWidth * BASE_SCALE
        }

        if (Math.abs(newScale - effectiveScale) / effectiveScale > SCALE_CHANGE_THRESHOLD) {
          effectiveScale = newScale
          // Re-render all pages when scale changes significantly
          clearRenderedPages()
          renderAllPages()
        }
      })
    }
  })

  function clearRenderedPages() {
    pagesEl.innerHTML = ''
    renderedPages.clear()
    sentenceElements.clear()
    intersectionObserver?.disconnect()
  }

  async function loadPDF() {
    loading = true
    error = ''
    try {
      const url = `http://localhost:8000/uploads/${bookId}.pdf`
      pdfDoc = await pdfjsLib.getDocument(url).promise
      pageCount = pdfDoc.numPages
      loading = false
      renderAllPages()
      setupIntersectionObserver()
      setupResizeObserver()
    } catch (e: any) {
      error = e?.message ?? 'Failed to load PDF'
      loading = false
    }
  }

  async function renderAllPages() {
    for (let i = 1; i <= pageCount; i++) {
      if (!pdfDoc) break
      await renderPage(i)
    }
  }

  async function renderPage(pageNum: number) {
    if (!pdfDoc || renderedPages.has(pageNum - 1)) return
    const page = await pdfDoc.getPage(pageNum)
    const viewport = page.getViewport({ scale: effectiveScale })

    const canvas = document.createElement('canvas')
    canvas.width = viewport.width
    canvas.height = viewport.height
    const ctx = canvas.getContext('2d')!
    await page.render({ canvasContext: ctx, viewport }).promise

    renderedPages.set(pageNum - 1, { canvas, viewport })

    const wrapper = document.createElement('div')
    wrapper.className = 'relative mb-4'
    wrapper.style.width = viewport.width + 'px'
    wrapper.style.height = viewport.height + 'px'
    wrapper.dataset.page = String(pageNum - 1)
    wrapper.appendChild(canvas)

    const overlay = document.createElement('div')
    overlay.className = 'absolute inset-0 pointer-events-auto'
    overlay.dataset.overlay = String(pageNum - 1)
    wrapper.appendChild(overlay)
    pagesEl?.appendChild(wrapper)
    drawHighlights(pageNum - 1)

    intersectionObserver?.observe(wrapper)
  }

  function drawHighlights(page: number) {
    if (!renderedPages.has(page)) return
    const overlay = pagesEl?.querySelector(`[data-overlay="${page}"]`) as HTMLElement
    if (!overlay) return
    overlay.innerHTML = ''
    const pageSentences = sentencesByPage.get(page) ?? []
    for (const s of pageSentences) {
      if (s.filtered) continue
      const PAD = 2
      const left   = s.x0 * effectiveScale - PAD
      const top    = s.y0 * effectiveScale - PAD
      const width  = (s.x1 - s.x0) * effectiveScale + PAD * 2
      const height = (s.y1 - s.y0) * effectiveScale + PAD * 2
      const div = document.createElement('div')
      div.className = [
        'absolute cursor-pointer transition-colors',
        s.index === currentIndex ? 'bg-yellow-200/60' : 'hover:bg-blue-100/40',
      ].join(' ')
      div.dataset.highlighted = s.index === currentIndex ? 'true' : 'false'
      div.style.left   = left + 'px'
      div.style.top    = top + 'px'
      div.style.width  = width + 'px'
      div.style.height = height + 'px'
      div.title = s.text
      div.dataset.index = String(s.index)
      div.onclick = () => onSentenceClick(s.index)
      sentenceElements.set(s.index, div)
      overlay.appendChild(div)
    }
  }

  // O(1) class toggle — no DOM rebuild on each currentIndex change
  $effect(() => {
    const idx = currentIndex
    const prev = sentenceElements.get(prevHighlightIndex)
    if (prev) {
      prev.classList.remove('bg-yellow-200/60')
      prev.setAttribute('data-highlighted', 'false')
      prev.classList.add('hover:bg-blue-100/40')
    }
    const curr = sentenceElements.get(idx)
    if (curr) {
      curr.classList.remove('hover:bg-blue-100/40')
      curr.classList.add('bg-yellow-200/60')
      curr.setAttribute('data-highlighted', 'true')
    }
    prevHighlightIndex = idx
  })

  // Auto-scroll to keep current sentence in view (debounced)
  $effect(() => {
    const idx = currentIndex
    if (scrollDebounce) clearTimeout(scrollDebounce)
    scrollDebounce = setTimeout(() => {
      const s = sentences.find(s => s.index === idx)
      if (!s) return
      const wrapper = pagesEl?.querySelector(`[data-page="${s.page}"]`) as HTMLElement
      if (!wrapper) return
      const y = s.y0 * effectiveScale
      scrollEl?.scrollTo({ top: wrapper.offsetTop + y - 200, behavior: 'smooth' })
    }, 150)
  })

  // Jump to specific page when pageToScroll changes
  $effect(() => {
    const p = pageToScroll
    if (p == null) return
    const wrapper = pagesEl?.querySelector(`[data-page="${p}"]`) as HTMLElement
    if (wrapper) scrollEl?.scrollTo({ top: wrapper.offsetTop, behavior: 'smooth' })
  })

  function setupIntersectionObserver() {
    intersectionObserver?.disconnect()
    let debounceTimer: ReturnType<typeof setTimeout> | null = null
    let pendingEntries: IntersectionObserverEntry[] = []
    
    intersectionObserver = new IntersectionObserver(
      (entries) => {
        // Collect entries for debouncing
        pendingEntries.push(...entries)
        
        // Clear existing timer
        if (debounceTimer) clearTimeout(debounceTimer)
        
        // Set new timer to process entries after 100ms
        debounceTimer = setTimeout(() => {
          // Process all collected entries
          let best = -1
          let bestRatio = 0
          for (const e of pendingEntries) {
            const p = Number((e.target as HTMLElement).dataset.page)
            if (e.intersectionRatio > bestRatio) { bestRatio = e.intersectionRatio; best = p }
          }
          if (best >= 0) onPageChange?.(best)
          
          // Clear pending entries after processing
          pendingEntries = []
        }, 100)
      },
      { root: scrollEl, threshold: [0, 0.25, 0.5, 0.75, 1.0] }
    )
  }

  function setupResizeObserver() {
    resizeObserver?.disconnect()
    resizeObserver = new ResizeObserver(entries => {
      if (entries[0] && entries[0].contentRect.width !== containerWidth) {
        containerWidth = entries[0].contentRect.width
      }
    })
    scrollEl && resizeObserver.observe(scrollEl)
  }

  onMount(() => {
    loadPDF()
  })
  onDestroy(() => {
    pdfDoc?.destroy()
    intersectionObserver?.disconnect()
    resizeObserver?.disconnect()
  })
</script>

<div class="relative h-full w-full overflow-auto bg-slate-100" bind:this={scrollEl}>
  {#if loading}
    <div class="flex h-full items-center justify-center gap-2 text-slate-500">
      <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading…
    </div>
  {:else if error}
    <div class="flex h-full items-center justify-center text-red-500">{error}</div>
  {/if}
  {#if buffering}
    <div class="absolute inset-0 bg-white/50 flex items-center justify-center z-20 pointer-events-none">
      <svg class="w-6 h-6 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
    </div>
  {/if}
  <div class="mx-auto flex flex-col items-center py-6" bind:this={pagesEl}></div>
</div>