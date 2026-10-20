<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import * as pdfjsLib from 'pdfjs-dist'
  import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
  import type { Sentence, WordBbox } from '$lib/api'
  import { getPdfUrl } from '$lib/api'
  import { bionifyTextToSegments, bionifyWord, toBionicOptions } from '$lib/utils/bionic-reading'
  import { settingsStore } from '$lib/stores/settings'

  pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

  let {
    bookId,
    sentences,
    currentIndex,
    onSentenceClick,
    onPageChange,
    buffering = false,
    pageToScroll = null,
    resumePage = null,
    searchMatches = [],
    currentSearchIndex = -1,
    highlightColor = '#fef08a',
    highlightEnabled = true,
    autoscroll = true,
    currentWordIndex = -1,
    bionicMode = false,
    bionicFixation = 1,
    bionicBoldRatio = 0.5,
    bionicMinWordLength,
    bionicSkipCommonWords,
  }: {
    bookId: string
    sentences: Sentence[]
    currentIndex: number
    currentWordIndex: number
    onSentenceClick: (index: number) => void
    onPageChange?: (page: number) => void
    /** Page to open at on first load (0-based), so a reopened book resumes there. */
    resumePage?: number | null
    buffering?: boolean
    pageToScroll?: number | null
    searchMatches?: number[]
    currentSearchIndex?: number
    highlightColor?: string
    highlightEnabled?: boolean
    autoscroll?: boolean
    bionicMode?: boolean
    bionicFixation?: number
    bionicBoldRatio?: number
    bionicMinWordLength?: number
    bionicSkipCommonWords?: boolean
  } = $props()

  function hexToRgba(hex: string, alpha: number): string {
    const h = hex.replace('#', '')
    const r = parseInt(h.substring(0, 2), 16)
    const g = parseInt(h.substring(2, 4), 16)
    const b = parseInt(h.substring(4, 6), 16)
    return `rgba(${r},${g},${b},${alpha})`
  }

  let sentenceElements = new Map<number, HTMLDivElement>()
  let searchMatchSet = new Set<number>()
  let wordElements = new Map<string, HTMLDivElement>()
  let prevWordKey = ''
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
  let renderObserver: IntersectionObserver | null = null
  let resizeObserver: ResizeObserver | null = null
  // Pages within this margin of the viewport are painted; the rest are sized
  // placeholders. Painting all 771 pages of a long PDF kept the main thread busy
  // for minutes and its canvases grew the tab past 5 GB.
  const RENDER_MARGIN = '150% 0px'
  /** Pages the render observer currently wants painted. */
  let nearPages = new Set<number>()
  /** Pages whose paint is in flight. */
  let paintingPages = new Set<number>()
  /** Bumped on every relayout (zoom, fit); a paint from an older layout is dropped. */
  let layoutToken = 0
  let bionicDebounce: ReturnType<typeof setTimeout> | null = null

  const MAX_WIDTH_PX = 900
  const BASE_SCALE = 1.5
  const SCALE_CHANGE_THRESHOLD = 0.05
  const WORD_HIGHLIGHT_ALPHA = 0.85
  const BIONIC_CANVAS_OPACITY = 0.3

  let containerWidth = $state(0)
  let pendingResumePage: number | null = null
  // Bumped whenever the overlays are rebuilt, so the highlight effects re-apply.
  let layoutVersion = $state(0)
  const RESIZE_SETTLE_MS = 150
  let effectiveScale = $state(BASE_SCALE)
  let zoomLevel = $state(1.0)
  let finalScale = $derived(effectiveScale * zoomLevel)

  // The reader route passes fixation and bold ratio but not the other two, so
  // those come from the settings store — the same store the Settings panel
  // writes. Without this, two of its four controls would change nothing in the
  // PDF overlay. An explicitly passed prop still wins.
  const storedBionic = $derived(toBionicOptions($settingsStore))
  const bionicOpts = $derived({
    fixationPoint: bionicFixation,
    boldRatio: bionicBoldRatio,
    minWordLength: bionicMinWordLength ?? storedBionic.minWordLength,
    skipCommonWords: bionicSkipCommonWords ?? storedBionic.skipCommonWords,
  })

  let sentencesByPage = $derived(
    sentences.reduce((acc, s) => {
      if (!acc.has(s.page)) acc.set(s.page, [])
      acc.get(s.page)!.push(s)
      return acc
    }, new Map<number, Sentence[]>())
  )

  // Fit to width once the resize settles: a drag fires many width changes, and each
  // one that re-renders every page makes the window feel slow.
  $effect(() => {
    if (!pdfDoc || containerWidth <= 0) return
    const width = containerWidth
    const timer = setTimeout(() => fitToWidth(width), RESIZE_SETTLE_MS)
    return () => clearTimeout(timer)
  })

  function fitToWidth(width: number) {
    // Natural page width: the first page's viewport at BASE_SCALE.
    const firstPageViewport = pdfDoc.getPage(1).then((p: any) => p.getViewport({ scale: BASE_SCALE }))
    Promise.resolve(firstPageViewport).then(viewport => {
      const naturalPageWidth = viewport.width
      let newScale = BASE_SCALE

      if (width < naturalPageWidth) {
        // Scale down to fit narrow screens
        newScale = width / naturalPageWidth * BASE_SCALE
      } else if (width > MAX_WIDTH_PX) {
        // Cap width on ultrawide screens
        newScale = MAX_WIDTH_PX / naturalPageWidth * BASE_SCALE
      }

      if (Math.abs(newScale - effectiveScale) / effectiveScale > SCALE_CHANGE_THRESHOLD) {
        effectiveScale = newScale
        // Lay the pages out again at the new scale; the window repaints itself.
        relayout()
      }
    })
  }

  function clearRenderedPages() {
    layoutToken++
    renderObserver?.disconnect()
    intersectionObserver?.disconnect()
    for (const { canvas } of renderedPages.values()) { canvas.width = 0; canvas.height = 0 }
    if (pagesEl) pagesEl.innerHTML = ''
    renderedPages.clear()
    sentenceElements.clear()
    wordElements.clear()
    nearPages = new Set()
    paintingPages = new Set()
  }

  /** Page whose wrapper is at the top of the scroll view, to keep it there across a relayout. */
  function topPage(): number | null {
    if (!scrollEl || !pagesEl) return null
    const top = scrollEl.scrollTop
    for (const w of pagesEl.children) {
      const el = w as HTMLElement
      if (el.offsetTop + el.offsetHeight > top) return Number(el.dataset.page)
    }
    return null
  }

  function relayout() {
    const keep = topPage()
    clearRenderedPages()
    void layoutPages(keep)
  }

  async function loadPDF() {
    loading = true
    error = ''
    try {
      pdfDoc = await pdfjsLib.getDocument(getPdfUrl(bookId)).promise
      pageCount = pdfDoc.numPages
      loading = false
      pendingResumePage = resumePage
      setupIntersectionObserver()
      setupResizeObserver()
      await layoutPages(null)
    } catch (e: any) {
      error = e?.message ?? 'Failed to load PDF'
      loading = false
    }
  }

  /**
   * One sized placeholder per page (the first page's size until a page is painted
   * with its own), so scrolling, page jumps and the resume position work at once;
   * the render observer paints only the pages near the viewport.
   */
  async function layoutPages(scrollToPage: number | null) {
    if (!pdfDoc || !pagesEl) return
    const token = layoutToken
    const first = await pdfDoc.getPage(1)
    if (token !== layoutToken || !pagesEl) return
    const est = first.getViewport({ scale: finalScale })
    setupRenderObserver()
    const frag = document.createDocumentFragment()
    for (let i = 0; i < pageCount; i++) {
      const wrapper = document.createElement('div')
      wrapper.className = 'relative mb-4'
      wrapper.style.width = est.width + 'px'
      wrapper.style.height = est.height + 'px'
      wrapper.dataset.page = String(i)

      const overlay = document.createElement('div')
      overlay.className = 'absolute inset-0 pointer-events-auto'
      overlay.dataset.overlay = String(i)
      wrapper.appendChild(overlay)

      const bionicOverlay = document.createElement('div')
      bionicOverlay.className = 'absolute inset-0 pointer-events-none'
      bionicOverlay.dataset.bionicOverlay = String(i)
      bionicOverlay.style.zIndex = '1'
      bionicOverlay.style.display = bionicMode ? '' : 'none'
      bionicOverlay.setAttribute('aria-hidden', 'true')
      wrapper.appendChild(bionicOverlay)
      frag.appendChild(wrapper)
    }
    pagesEl.appendChild(frag)
    for (const w of pagesEl.children) {
      intersectionObserver?.observe(w)
      renderObserver?.observe(w)
    }
    // Open where the reader was (or where a relayout was), instantly.
    const target = pendingResumePage ?? scrollToPage
    pendingResumePage = null
    if (target !== null) {
      const w = pagesEl.querySelector(`[data-page="${target}"]`) as HTMLElement | null
      if (w) scrollEl?.scrollTo({ top: w.offsetTop })
    }
    layoutVersion++
  }

  function setupRenderObserver() {
    renderObserver?.disconnect()
    renderObserver = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const p = Number((e.target as HTMLElement).dataset.page)
          if (e.isIntersecting) {
            nearPages.add(p)
            void renderPage(p + 1)
          } else {
            nearPages.delete(p)
            releasePage(p)
          }
        }
      },
      { root: scrollEl, rootMargin: RENDER_MARGIN },
    )
  }

  /** Paint one page into its placeholder, if it is still wanted when the paint ends. */
  async function renderPage(pageNum: number) {
    const idx = pageNum - 1
    if (!pdfDoc || renderedPages.has(idx) || paintingPages.has(idx)) return
    const token = layoutToken
    paintingPages.add(idx)
    try {
      const page = await pdfDoc.getPage(pageNum)
      if (token !== layoutToken || !nearPages.has(idx)) return
      const viewport = page.getViewport({ scale: finalScale })
      const canvas = document.createElement('canvas')
      canvas.width = viewport.width
      canvas.height = viewport.height
      const ctx = canvas.getContext('2d')!
      await page.render({ canvasContext: ctx, viewport }).promise
      const wrapper = pagesEl?.querySelector(`[data-page="${idx}"]`) as HTMLElement | null
      if (token !== layoutToken || !nearPages.has(idx) || !wrapper) {
        canvas.width = 0
        canvas.height = 0
        return
      }
      wrapper.style.width = viewport.width + 'px'
      wrapper.style.height = viewport.height + 'px'
      wrapper.insertBefore(canvas, wrapper.firstChild)
      renderedPages.set(idx, { canvas, viewport })
      drawHighlights(idx)
      drawBionicText(idx)
      if (bionicMode) {
        canvas.style.opacity = String(BIONIC_CANVAS_OPACITY)
        canvas.style.transition = 'opacity 0.3s ease'
      }
      // The highlight effects re-apply to the new page's overlays.
      layoutVersion++
    } finally {
      if (token === layoutToken) paintingPages.delete(idx)
    }
  }

  /** Drop a page that left the window: its canvas memory, overlays and lookups. */
  function releasePage(idx: number) {
    const r = renderedPages.get(idx)
    if (!r) return
    r.canvas.width = 0
    r.canvas.height = 0
    r.canvas.remove()
    renderedPages.delete(idx)
    const overlay = pagesEl?.querySelector(`[data-overlay="${idx}"]`) as HTMLElement | null
    if (overlay) overlay.innerHTML = ''
    const bionicOverlay = pagesEl?.querySelector(`[data-bionic-overlay="${idx}"]`) as HTMLElement | null
    if (bionicOverlay) bionicOverlay.innerHTML = ''
    for (const s of sentencesByPage.get(idx) ?? []) {
      sentenceElements.delete(s.index)
      for (let wi = 0; wi < (s.words?.length ?? 0); wi++) wordElements.delete(wordKey(s.index, wi))
    }
  }

  function wordKey(sentenceIndex: number, wordIndex: number): string {
    return `${sentenceIndex}:${wordIndex}`
  }

  function createWordDiv(word: WordBbox, scale: number, wordIndex: number, sentenceIndex: number): HTMLDivElement {
    const div = document.createElement('div')
    div.className = 'absolute pointer-events-none'
    div.style.left = (word.x0 * scale) + 'px'
    div.style.top = (word.y0 * scale) + 'px'
    div.style.width = ((word.x1 - word.x0) * scale) + 'px'
    div.style.height = ((word.y1 - word.y0) * scale) + 'px'
    div.dataset.wordIndex = String(wordIndex)
    div.dataset.sentenceIndex = String(sentenceIndex)
    return div
  }

  function diffSets(prev: Set<number>, next: Set<number>): { added: number[]; removed: number[] } {
    return {
      added:   next.size  ? [...next].filter(i => !prev.has(i))  : [],
      removed: prev.size  ? [...prev].filter(i => !next.has(i))  : [],
    }
  }

  // Colours come from the --search-* tokens in app.css, which derive from the
  // theme's accent, so matches follow light/dark instead of a fixed blue/green.
  // Fill only, no outline: outlines were replaced by fills on purpose (305610e).
  function applySearchHighlight(el: HTMLDivElement, isCurrent: boolean): void {
    el.style.backgroundColor = isCurrent ? 'var(--search-current-bg)' : 'var(--search-match-bg)'
  }

  function clearSearchHighlight(el: HTMLDivElement): void {
    el.style.backgroundColor = ''
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
      const left   = s.x0 * finalScale - PAD
      const top    = s.y0 * finalScale - PAD
      const width  = (s.x1 - s.x0) * finalScale + PAD * 2
      const height = (s.y1 - s.y0) * finalScale + PAD * 2
      const div = document.createElement('div')
      div.className = 'absolute cursor-pointer transition-colors hover:bg-accent-soft'
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
      if (s.index === currentIndex && highlightEnabled) {
        div.style.backgroundColor = `color-mix(in oklab, ${highlightColor} calc(var(--highlight-alpha) * 100%), transparent)`
      } else if (searchMatchSet.has(s.index)) {
        // A page painted after the search ran still shows its matches.
        applySearchHighlight(div, searchMatches.indexOf(s.index) === currentSearchIndex)
      }
      if (s.words) {
        for (let wi = 0; wi < s.words.length; wi++) {
          const wd = createWordDiv(s.words[wi], finalScale, wi, s.index)
          wordElements.set(wordKey(s.index, wi), wd)
          overlay.appendChild(wd)
        }
      }
    }
  }

  function drawBionicText(page: number) {
    const bionicOverlay = pagesEl?.querySelector(`[data-bionic-overlay="${page}"]`) as HTMLElement
    if (!bionicOverlay) return
    bionicOverlay.innerHTML = ''
    if (!bionicMode) return
    const pageSentences = sentencesByPage.get(page) ?? []
    if (pageSentences.length === 0) return

    for (const s of pageSentences) {
      if (s.filtered) continue

      if (s.words && s.words.length > 0) {
        // Path A: word-level rendering using per-word bounding boxes (accurate positioning)
        const textWords = s.text.split(/\s+/).filter(w => w.length > 0)
        const count = Math.min(textWords.length, s.words.length)
        for (let i = 0; i < count; i++) {
          const word = s.words[i]
          const wordText = textWords[i]
          const fontSize = Math.max(6, (word.y1 - word.y0) * finalScale * 0.8)
          const bw = bionifyWord(wordText, bionicOpts)
          const span = document.createElement('span')
          span.className = 'absolute'
          span.style.left = (word.x0 * finalScale) + 'px'
          span.style.top = (word.y0 * finalScale) + 'px'
          span.style.fontSize = fontSize + 'px'
          span.style.lineHeight = '1'
          span.style.whiteSpace = 'nowrap'
          if (bw.bold) {
            const boldNode = document.createElement('strong')
            boldNode.textContent = bw.bold
            span.appendChild(boldNode)
          }
          if (bw.rest) {
            const restNode = document.createElement('span')
            restNode.textContent = bw.rest
            span.appendChild(restNode)
          }
          bionicOverlay.appendChild(span)
        }
      } else {
        // Path B: sentence-level fallback (EPUBs, text books, old PDFs without word data)
        const segments = bionifyTextToSegments(s.text, bionicOpts)
        const fontSize = Math.min(22, Math.max(8, (s.y1 - s.y0) * finalScale * 0.85))
        const span = document.createElement('span')
        span.className = 'absolute'
        span.style.left = (s.x0 * finalScale) + 'px'
        span.style.top = (s.y0 * finalScale) + 'px'
        span.style.fontSize = fontSize + 'px'
        span.style.lineHeight = '1'
        span.style.whiteSpace = 'pre'
        span.style.overflow = 'hidden'
        span.style.maxWidth = ((s.x1 - s.x0) * finalScale) + 'px'
        for (const seg of segments) {
          const node = document.createElement(seg.bold ? 'strong' : 'span')
          node.textContent = seg.text
          span.appendChild(node)
        }
        bionicOverlay.appendChild(span)
      }
    }
  }

  function updateCanvasOpacity() {
    for (const [page] of renderedPages) {
      const wrapper = pagesEl?.querySelector(`[data-page="${page}"]`) as HTMLElement
      if (!wrapper) continue
      const canvas = wrapper.querySelector('canvas') as HTMLElement
      if (canvas) {
        canvas.style.opacity = bionicMode ? String(BIONIC_CANVAS_OPACITY) : '1'
        canvas.style.transition = 'opacity 0.3s ease'
      }
    }
  }

  // O(1) style toggle — no DOM rebuild on each currentIndex change
  $effect(() => {
    const idx = currentIndex
    void layoutVersion
    const prev = sentenceElements.get(prevHighlightIndex)
    if (prev) {
      prev.style.backgroundColor = ''
      prev.setAttribute('data-highlighted', 'false')
    }
    const curr = sentenceElements.get(idx)
    if (curr) {
      curr.style.backgroundColor = highlightEnabled ? `color-mix(in oklab, ${highlightColor} calc(var(--highlight-alpha) * 100%), transparent)` : ''
      curr.setAttribute('data-highlighted', highlightEnabled ? 'true' : 'false')
    }
    prevHighlightIndex = idx
  })

  $effect(() => {
    const newKey = currentWordIndex >= 0 ? wordKey(currentIndex, currentWordIndex) : ''
    void layoutVersion
    const prev = wordElements.get(prevWordKey)
    if (prev) prev.style.backgroundColor = ''
    const curr = wordElements.get(newKey)
    if (curr) curr.style.backgroundColor = `color-mix(in oklab, ${highlightColor} calc(var(--word-alpha) * 100%), transparent)`
    prevWordKey = newKey
  })

  // Search match highlighting
  $effect(() => {
    void currentIndex
    const nextSet = new Set(searchMatches)
    const { added, removed } = diffSets(searchMatchSet, nextSet)
    for (const i of removed) {
      const el = sentenceElements.get(i)
      if (el) clearSearchHighlight(el)
    }
    for (const i of added) {
      const el = sentenceElements.get(i)
      if (!el) continue
      if (el.getAttribute('data-highlighted') === 'true') continue
      applySearchHighlight(el, searchMatches.indexOf(i) === currentSearchIndex)
    }
    // Update current search highlight when currentSearchIndex changes
    if (searchMatches.length > 0) {
      const prevCurrentEl = searchMatches[currentSearchIndex - 1] !== undefined
        ? sentenceElements.get(searchMatches[currentSearchIndex - 1])
        : undefined
      if (prevCurrentEl) applySearchHighlight(prevCurrentEl, false)
      const currEl = sentenceElements.get(searchMatches[currentSearchIndex])
      if (currEl && currEl.getAttribute('data-highlighted') !== 'true') applySearchHighlight(currEl, true)
    }
    searchMatchSet = nextSet
  })

  // Auto-scroll to keep current sentence in view (debounced).
  // Gate on !buffering so the PDF doesn't jump pages while Kokoro is still loading
  // the first chunk — the scroll fires when audio is actually confirmed ready.
  $effect(() => {
    const idx = currentIndex
    if (!autoscroll || buffering) return
    if (scrollDebounce) clearTimeout(scrollDebounce)
    scrollDebounce = setTimeout(() => {
      const s = sentences.find(s => s.index === idx)
      if (!s) return
      const wrapper = pagesEl?.querySelector(`[data-page="${s.page}"]`) as HTMLElement
      if (!wrapper) return
      const y = s.y0 * finalScale
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

  // Redraw highlights when highlightEnabled changes
  $effect(() => {
    void highlightEnabled
    // A new `sentences` array (another book, a reload) yields a new
    // sentencesByPage; rebuild every overlay instead of keeping the old book's.
    void sentencesByPage
    for (const [page] of renderedPages) {
      drawHighlights(page)
    }
  })

  // Re-render bionic text and toggle canvas opacity when bionic settings change
  $effect(() => {
    void bionicMode
    void bionicFixation
    void bionicBoldRatio

    // Always update visibility and canvas opacity immediately (cheap)
    for (const [page] of renderedPages) {
      const overlay = pagesEl?.querySelector(`[data-bionic-overlay="${page}"]`) as HTMLElement
      if (overlay) {
        overlay.style.display = bionicMode ? '' : 'none'
      }
    }
    updateCanvasOpacity()

    // Debounce expensive text re-render (slider drags fire ~60 events/sec)
    if (bionicDebounce) clearTimeout(bionicDebounce)
    bionicDebounce = bionicMode ? setTimeout(() => {
      for (const [page] of renderedPages) {
        drawBionicText(page)
      }
    }, 80) : null
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

  function applyZoom(level: number) {
    zoomLevel = level
    relayout()
  }

  function zoomIn() {
    applyZoom(Math.min(3.0, +(zoomLevel + 0.15).toFixed(2)))
  }

  function zoomOut() {
    applyZoom(Math.max(0.5, +(zoomLevel - 0.15).toFixed(2)))
  }

  function zoomReset() {
    applyZoom(1.0)
  }

  const INPUT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

  function onKeyDown(e: KeyboardEvent) {
    if (!(e.target instanceof Element)) return
    if (!e.ctrlKey) return
    if (INPUT_TAGS.has(e.target.tagName)) return
    if (e.key === '=' || e.key === '+') { e.preventDefault(); zoomIn() }
    else if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomOut() }
    else if (e.key === '0') { e.preventDefault(); zoomReset() }
  }

  onMount(() => {
    loadPDF()
    window.addEventListener('keydown', onKeyDown)
  })
  onDestroy(() => {
    window.removeEventListener('keydown', onKeyDown)
    if (bionicDebounce) clearTimeout(bionicDebounce)
    layoutToken++
    pdfDoc?.destroy()
    intersectionObserver?.disconnect()
    renderObserver?.disconnect()
    resizeObserver?.disconnect()
  })
</script>

<div class="relative h-full w-full overflow-auto bg-surface-sunken" bind:this={scrollEl}>
  {#if loading}
    <div class="flex h-full items-center justify-center gap-2 text-fg-muted">
      <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
      Loading…
    </div>
  {:else if error}
    <div class="flex h-full items-center justify-center text-danger">{error}</div>
  {/if}
  {#if buffering}
    <!-- raw z-20: an opaque veil is not a scrim (nothing dismisses it) and not
         a panel; the scale has no layer for it. -->
    <div class="absolute inset-0 bg-surface/50 flex items-center justify-center z-20 pointer-events-none">
      <svg class="w-6 h-6 animate-spin text-accent" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
      </svg>
    </div>
  {/if}
  <div class="mx-auto flex flex-col items-center py-6" bind:this={pagesEl}></div>
  <div class="fixed bottom-32 right-4 flex items-center gap-1 bg-surface-raised/90 backdrop-blur rounded-lg shadow-2 border border-border px-2 py-1.5 z-40">
    <button
      onclick={zoomOut}
      class="p-1 rounded hover:bg-surface-sunken text-fg-muted"
      aria-label="Zoom out"
      disabled={zoomLevel <= 0.5}
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4" />
      </svg>
    </button>
    <span class="text-xs font-medium text-fg-muted min-w-[3rem] text-center select-none">
      {Math.round(zoomLevel * 100)}%
    </span>
    <button
      onclick={zoomIn}
      class="p-1 rounded hover:bg-surface-sunken text-fg-muted"
      aria-label="Zoom in"
      disabled={zoomLevel >= 3.0}
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
      </svg>
    </button>
    <button
      onclick={zoomReset}
      class="p-1 rounded hover:bg-surface-sunken text-fg-subtle ml-1 text-xs font-medium"
      aria-label="Reset zoom"
    >
      Fit
    </button>
  </div>
</div>