import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte'
import LibraryCard from '$lib/components/LibraryCard.svelte'
import type { Book } from '$lib/api'

const BOOK: Book = {
  id: 'book-1',
  title: 'Deep Work',
  // No endpoint can set `Book.author`, so this is the ordinary case.
  author: null,
  file_type: 'PDF',
  page_count: 200,
  folder_id: null,
}

function renderCard(props: Record<string, unknown> = {}) {
  const onClick = vi.fn()
  render(LibraryCard, { props: { book: BOOK, onClick, ...props } })
  return { onClick }
}

describe('LibraryCard states', () => {
  beforeEach(cleanup)

  describe('cover', () => {
    it('stands a placeholder in for cover art the app cannot derive', () => {
      renderCard()

      const placeholder = document.querySelector('[data-cover-placeholder="book-1"]')
      expect(placeholder).toBeTruthy()
      expect(placeholder?.textContent).toContain('No cover')
      // The real file type still shows through the placeholder.
      expect(placeholder?.textContent).toContain('PDF')
    })
  })

  describe('missing author', () => {
    it('says the author was not detected, as the default look', () => {
      renderCard()

      expect(screen.getByText('Author not detected')).toBeTruthy()
      expect(screen.queryByText('Unknown')).toBeNull()
    })

    it('reveals the author when the book has one', () => {
      renderCard({ book: { ...BOOK, author: 'Cal Newport' } })

      expect(screen.getByText('Cal Newport')).toBeTruthy()
      expect(screen.queryByText('Author not detected')).toBeNull()
    })
  })

  describe('progress', () => {
    it('draws nothing when the book has no reading position', () => {
      renderCard()

      expect(screen.queryByRole('progressbar')).toBeNull()
      expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull()
    })

    it('draws nothing when the sentence total is unknown', () => {
      // The index is real but 30 sentences of what? No bar beats a guessed one.
      renderCard({ sentenceIndex: 30, totalSentences: null })

      expect(screen.queryByRole('progressbar')).toBeNull()
      expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull()
    })

    it('draws nothing for sentence 0, which is also "never started"', () => {
      renderCard({ sentenceIndex: 0, totalSentences: 200 })

      expect(screen.queryByRole('progressbar')).toBeNull()
    })

    it('derives the percentage from the real index and total', () => {
      renderCard({ sentenceIndex: 30, totalSentences: 120 })

      const bar = screen.getByRole('progressbar', { name: 'Reading progress for Deep Work' })
      expect(bar.getAttribute('aria-valuenow')).toBe('25')
      expect(bar.getAttribute('aria-valuetext')).toBe('25%')
      expect(screen.getByText('25%')).toBeTruthy()
    })

    it('calls the last sentence finished rather than 100%', () => {
      renderCard({ sentenceIndex: 119, totalSentences: 120 })

      const bar = screen.getByRole('progressbar')
      expect(bar.getAttribute('aria-valuetext')).toBe('Finished')
      expect(screen.getByText('Finished')).toBeTruthy()
    })
  })

  describe('resume', () => {
    it('opens the book, which is what resuming means', async () => {
      const { onClick } = renderCard({ sentenceIndex: 30, totalSentences: 120 })

      await fireEvent.click(screen.getByRole('button', { name: 'Resume Deep Work' }))

      expect(onClick).toHaveBeenCalledTimes(1)
      expect(onClick).toHaveBeenCalledWith('book-1')
    })

    it('names the book it resumes, since a grid holds many', () => {
      renderCard({ sentenceIndex: 30, totalSentences: 120 })

      expect(screen.getByRole('button', { name: 'Resume Deep Work' })).toBeTruthy()
    })
  })

  describe('card itself', () => {
    it('still opens the book when the card is clicked', async () => {
      const { onClick } = renderCard()

      await fireEvent.click(screen.getByLabelText('Deep Work by Unknown'))

      expect(onClick).toHaveBeenCalledWith('book-1')
    })

    it('keeps the controls the folder UI depends on', () => {
      renderCard({ onMove: vi.fn(), onDelete: vi.fn() })

      expect(screen.getByLabelText('Book options for Deep Work')).toBeTruthy()
    })

    it('gives every control a touch target of at least 44px', () => {
      renderCard({ sentenceIndex: 30, totalSentences: 120, onMove: vi.fn(), onDelete: vi.fn() })

      // jsdom has no layout, so the required size is asserted on the classes
      // that produce it (min-h-11 / h-11 are 2.75rem = 44px).
      const sizes = [
        screen.getByLabelText('Book options for Deep Work').className,
        screen.getByRole('button', { name: 'Resume Deep Work' }).className,
      ]
      for (const className of sizes) {
        expect(className).toMatch(/\bmin-h-11\b|\bh-11\b/)
      }
    })
  })
})
