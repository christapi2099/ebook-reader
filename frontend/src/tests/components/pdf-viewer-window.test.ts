import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/svelte'
import { tick } from 'svelte'

// A 60-page "PDF": every page 600x800 at scale 1.5.
const PAGES = 60
const renders: number[] = []
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({
      numPages: PAGES,
      getPage: (n: number) => Promise.resolve({
        getViewport: ({ scale }: { scale: number }) => ({ width: (600 * scale) / 1.5, height: (800 * scale) / 1.5 }),
        render: () => { renders.push(n); return { promise: Promise.resolve() } },
      }),
      destroy: () => {},
    }),
  }),
}))
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: 'worker.js' }))

// IntersectionObserver the test drives: `show(pages)` makes exactly those pages
// intersect every observer that watches them.
type Obs = { cb: IntersectionObserverCallback; targets: Set<Element>; margin: string }
const observers: Obs[] = []
class FakeIO {
  o: Obs
  constructor(cb: IntersectionObserverCallback, opts?: IntersectionObserverInit) {
    this.o = { cb, targets: new Set(), margin: opts?.rootMargin ?? '' }
    observers.push(this.o)
  }
  observe(t: Element) { this.o.targets.add(t) }
  unobserve(t: Element) { this.o.targets.delete(t) }
  disconnect() { this.o.targets.clear() }
  takeRecords() { return [] }
}
function show(pages: number[]) {
  for (const o of observers) {
    const entries = [...o.targets].map(t => {
      const on = pages.includes(Number((t as HTMLElement).dataset.page))
      return { target: t, isIntersecting: on, intersectionRatio: on ? 1 : 0 } as unknown as IntersectionObserverEntry
    })
    if (entries.length) o.cb(entries, o as never)
  }
}

import PDFViewer from '$lib/components/PDFViewer.svelte'

const sentences = Array.from({ length: PAGES * 4 }, (_, i) => ({
  index: i, text: `sentence ${i} here`, page: Math.floor(i / 4), x0: 10, y0: 10 + (i % 4) * 50, x1: 300, y1: 40 + (i % 4) * 50,
  filtered: false, chapter: 0, chapter_title: null,
  words: [{ x0: 10, y0: 10, x1: 50, y1: 30 }, { x0: 60, y0: 10, x1: 120, y1: 30 }],
})) as never[]

async function settle() { for (let i = 0; i < 30; i++) { await Promise.resolve(); await tick() } }
const canvases = () => document.querySelectorAll('canvas').length
const overlayDivs = (page: number) => document.querySelectorAll(`[data-overlay="${page}"] [data-index]`).length

describe('PDFViewer renders a window of pages, not the whole book', () => {
  beforeEach(() => {
    cleanup(); observers.length = 0; renders.length = 0
    vi.stubGlobal('IntersectionObserver', FakeIO)
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    HTMLCanvasElement.prototype.getContext = (() => ({})) as never
    Element.prototype.scrollTo = function () {} as never
  })
  afterEach(() => { vi.unstubAllGlobals() })

  function mount(props: Record<string, unknown> = {}) {
    const onSentenceClick = vi.fn()
    render(PDFViewer, { props: { bookId: 'b', sentences, currentIndex: 0, currentWordIndex: -1, onSentenceClick, ...props } })
    return { onSentenceClick }
  }

  it('lays out a sized placeholder per page and paints none until it is near the viewport', async () => {
    mount()
    await settle()
    const wrappers = document.querySelectorAll('[data-page]')
    expect(wrappers.length).toBe(PAGES)
    expect((wrappers[10] as HTMLElement).style.height).toBe('800px')
    expect(canvases()).toBe(0)
    show([0, 1, 2])
    await settle()
    expect(canvases()).toBe(3)
    expect(overlayDivs(1)).toBe(4)
    expect(overlayDivs(5)).toBe(0)
  })

  it('releases pages that leave the window: canvas, overlays and their highlight entries', async () => {
    mount()
    await settle()
    show([0, 1, 2])
    await settle()
    show([20, 21])
    await settle()
    expect(canvases()).toBe(2)
    expect(overlayDivs(0)).toBe(0)
    expect(overlayDivs(20)).toBe(4)
    expect(document.querySelector('[data-page="0"] canvas')).toBeNull()
  })

  it('a page painted later shows the current sentence highlighted, and clicks seek', async () => {
    const { onSentenceClick } = mount({ currentIndex: 85 })
    await settle()
    show([21])
    await settle()
    const el = document.querySelector('[data-index="85"]') as HTMLElement
    expect(el.dataset.highlighted).toBe('true')
    el.click()
    expect(onSentenceClick).toHaveBeenCalledWith(85)
  })

  it('a page that left before its paint finished is not kept', async () => {
    mount()
    await settle()
    show([3])
    show([])
    await settle()
    expect(canvases()).toBe(0)
  })
})
