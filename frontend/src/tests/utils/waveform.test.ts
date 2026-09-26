import { describe, it, expect } from 'vitest'
import {
  bucketPeaks,
  barHeights,
  playedFraction,
  WAVEFORM_BUCKETS,
  WAVEFORM_MIN_FRACTION,
} from '$lib/utils/waveform'

describe('bucketPeaks', () => {
  it('returns one bar per bucket', () => {
    expect(bucketPeaks(new Float32Array(480), 48)).toHaveLength(48)
    expect(bucketPeaks(new Float32Array(480))).toHaveLength(WAVEFORM_BUCKETS)
  })

  it('scales the loudest bucket to 1 and keeps the shape', () => {
    // First half quiet (0.25), second half loud (1.0).
    const samples = new Float32Array(100)
    samples.fill(0.25, 0, 50)
    samples.fill(1.0, 50, 100)

    const peaks = bucketPeaks(samples, 10)

    expect(peaks[0]).toBeCloseTo(0.25)
    expect(peaks[9]).toBeCloseTo(1)
    expect(Math.max(...peaks)).toBe(1)
  })

  it('uses magnitude, so a negative peak counts as loud', () => {
    const samples = new Float32Array([-1, 0, 0, -1])
    expect(bucketPeaks(samples, 2)).toEqual([1, 1])
  })

  it('reports silence as zeros rather than dividing by zero', () => {
    const peaks = bucketPeaks(new Float32Array(64), 8)
    expect(peaks).toHaveLength(8)
    expect(peaks.every(p => p === 0)).toBe(true)
    expect(peaks.every(p => Number.isFinite(p))).toBe(true)
  })

  it('handles an empty signal and a non-positive bucket count', () => {
    expect(bucketPeaks(new Float32Array(0), 4)).toEqual([0, 0, 0, 0])
    expect(bucketPeaks(new Float32Array(10), 0)).toEqual([])
    expect(bucketPeaks(new Float32Array(10), -3)).toEqual([])
  })

  it('spreads a shorter signal across every bucket', () => {
    // Two samples, four buckets: no bucket may be skipped or empty.
    const peaks = bucketPeaks(new Float32Array([0.5, 0.25]), 4)
    expect(peaks).toHaveLength(4)
    expect(peaks.every(p => p > 0)).toBe(true)
    expect(Math.max(...peaks)).toBe(1)
  })

  it('covers the whole signal, including the final sample', () => {
    const samples = new Float32Array(10)
    samples[9] = 1
    expect(Math.max(...bucketPeaks(samples, 3))).toBe(1)
  })
})

describe('barHeights', () => {
  it('keeps every bar visible, even a silent one', () => {
    const heights = barHeights([0, 0.5, 1])
    expect(heights[0]).toBe(Math.round(WAVEFORM_MIN_FRACTION * 100))
    expect(heights[1]).toBeGreaterThan(heights[0])
    expect(heights[2]).toBe(100)
  })

  it('clamps out-of-range peaks', () => {
    expect(barHeights([-5, 5])).toEqual(barHeights([0, 1]))
  })

  it('returns integers in 0..100', () => {
    for (const height of barHeights([0.13, 0.37, 0.91])) {
      expect(Number.isInteger(height)).toBe(true)
      expect(height).toBeGreaterThanOrEqual(0)
      expect(height).toBeLessThanOrEqual(100)
    }
  })
})

describe('playedFraction', () => {
  it('is the element’s own clock over the real duration', () => {
    expect(playedFraction(0, 4)).toBe(0)
    expect(playedFraction(1, 4)).toBe(0.25)
    expect(playedFraction(4, 4)).toBe(1)
  })

  it('clamps past the end and inside the start', () => {
    expect(playedFraction(9, 4)).toBe(1)
    expect(playedFraction(-1, 4)).toBe(0)
  })

  it('is 0 while the duration is unknown', () => {
    // `duration` is NaN until media metadata arrives — not a zero-length file.
    expect(playedFraction(3, NaN)).toBe(0)
    expect(playedFraction(3, 0)).toBe(0)
    expect(playedFraction(NaN, 4)).toBe(0)
    expect(playedFraction(Infinity, 4)).toBe(0)
  })
})
