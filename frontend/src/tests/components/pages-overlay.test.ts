import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/svelte'
import PagesOverlay from '$lib/components/PagesOverlay.svelte'
import { buildPageIndex } from '$lib/utils/page-index'
import type { Sentence } from '$lib/api'

function sentence(index: number): Sentence {
  return {
    index,
    text: `Sentence ${index}`,
    page: 0,
    x0: 0,
    y0: 0,
    x1: 0,
    y1: 0,
    filtered: false,
    chapter: 0,
  }
}

const PAGES = buildPageIndex(Array.from({ length: 5 }, (_, index) => sentence(index)), 5, false)

function renderOverlay(props: Record<string, unknown> = {}) {
  const onJump = vi.fn()
  const onClose = vi.fn()
  render(PagesOverlay, {
    props: { pages: PAGES, currentPage: 2, bookTitle: 'Deep Work', onJump, onClose, ...props },
  })
  return { onJump, onClose }
}

function pageEntry(page: number) {
  const entry = document.querySelector(`[data-page-entry="${page}"]`)
  if (!entry) throw new Error(`No page entry ${page}`)
  return entry as HTMLElement
}

describe('PagesOverlay', () => {
  beforeEach(cleanup)

  it('lists every page of the book with the text it starts at', () => {
    renderOverlay()

    expect(document.querySelectorAll('[data-page-entry]')).toHaveLength(5)
    expect(within(pageEntry(3)).getByText('Sentence 3')).toBeTruthy()
    // Pages are shown 1-based; the entry keys off the route's 0-based page.
    expect(within(pageEntry(4)).getByText('5')).toBeTruthy()
  })

  it('reports the real position in the header', () => {
    renderOverlay({ currentPage: 2 })

    expect(screen.getByText(/page 3 of 5/)).toBeTruthy()
    expect(screen.getByText(/Deep Work/)).toBeTruthy()
  })

  it('marks the current page and is a dialog with a name', () => {
    renderOverlay({ currentPage: 2 })

    const marked = document.querySelectorAll('[data-page-entry][aria-current="page"]')
    expect(marked).toHaveLength(1)
    expect(marked[0]).toBe(pageEntry(2))
    expect(screen.getByRole('dialog', { name: 'Pages' })).toBeTruthy()
  })

  it('moves focus to the current page when it opens', () => {
    renderOverlay({ currentPage: 2 })

    expect(document.activeElement).toBe(pageEntry(2))
  })

  it('hands the chosen page to the route, which seeks to it', async () => {
    const { onJump } = renderOverlay()

    await fireEvent.click(pageEntry(4))

    expect(onJump).toHaveBeenCalledTimes(1)
    expect(onJump).toHaveBeenCalledWith(4)
  })

  it('closes on Escape', async () => {
    const { onClose } = renderOverlay()

    await fireEvent.keyDown(document, { key: 'Escape' })

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes when the backdrop is clicked, but not when the list is', async () => {
    const { onClose } = renderOverlay()

    await fireEvent.click(screen.getByText('Sentence 3'))
    expect(onClose).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByRole('dialog'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('says so when there are no pages to skim yet', () => {
    renderOverlay({ pages: [] })

    expect(document.querySelectorAll('[data-page-entry]')).toHaveLength(0)
    expect(screen.getByText('This book has no pages to skim yet.')).toBeTruthy()
  })
})
