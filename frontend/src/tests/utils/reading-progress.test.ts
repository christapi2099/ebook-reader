import { describe, it, expect } from 'vitest'
import { deriveReadingProgress } from '$lib/utils/reading-progress'

describe('deriveReadingProgress', () => {
  describe('when a real ratio exists', () => {
    it('derives the percentage from the sentence index and the sentence total', () => {
      const progress = deriveReadingProgress(30, 120)

      expect(progress).toEqual({
        sentenceIndex: 30,
        totalSentences: 120,
        percent: 25,
        finished: false,
        label: '25%',
      })
    })

    it('rounds a fractional percentage rather than showing decimals', () => {
      expect(deriveReadingProgress(1, 3)?.percent).toBe(33)
    })

    it('treats the last sentence as finished, not as a percentage', () => {
      const progress = deriveReadingProgress(119, 120)

      expect(progress?.finished).toBe(true)
      // 119/120 is 99%, so "finished" cannot be inferred from the percentage;
      // it is the index that decides (handoff §3.1).
      expect(progress?.percent).toBe(99)
      expect(progress?.label).toBe('Finished')
    })

    it('reports a position in the middle of a short book as progress', () => {
      const progress = deriveReadingProgress(1, 3)

      expect(progress?.percent).toBe(33)
      expect(progress?.finished).toBe(false)
    })

    it('clamps an index past the end instead of showing more than 100%', () => {
      const progress = deriveReadingProgress(500, 120)

      expect(progress?.sentenceIndex).toBe(119)
      expect(progress?.finished).toBe(true)
      expect(progress?.percent).toBe(99)
    })
  })

  describe('when it must show nothing', () => {
    it('shows nothing while the sentence total is unknown', () => {
      // The bar would need a denominator; guessing one is what rule 7 forbids.
      expect(deriveReadingProgress(30, null)).toBeNull()
      expect(deriveReadingProgress(30, undefined)).toBeNull()
    })

    it('shows nothing for sentence 0, which a missing progress row also reports', () => {
      expect(deriveReadingProgress(0, 120)).toBeNull()
    })

    it('shows nothing for a non-positive or non-finite total', () => {
      expect(deriveReadingProgress(30, 0)).toBeNull()
      expect(deriveReadingProgress(30, -5)).toBeNull()
      expect(deriveReadingProgress(30, Number.POSITIVE_INFINITY)).toBeNull()
      expect(deriveReadingProgress(30, Number.NaN)).toBeNull()
    })

    it('shows nothing for a missing index', () => {
      expect(deriveReadingProgress(null, 120)).toBeNull()
      expect(deriveReadingProgress(undefined, 120)).toBeNull()
      expect(deriveReadingProgress(Number.NaN, 120)).toBeNull()
    })
  })
})
