/**
 * Waveform geometry for the voice preview dock.
 *
 * The bars come from the preview audio itself: the route hands the decoded
 * samples of the real bytes to `bucketPeaks`, so a preview of a quiet voice
 * looks quiet. Nothing here invents an amplitude, and nothing here runs a timer
 * — how much of the strip is filled comes from the audio element's own clock.
 */

/** Number of bars in the dock's strip. */
export const WAVEFORM_BUCKETS = 48

/** Every bar keeps at least this fraction of the height, so silence is visible. */
export const WAVEFORM_MIN_FRACTION = 0.08

/**
 * Peak envelope: `buckets` bars, each the loudest absolute sample in its slice,
 * scaled so the loudest bar is 1. Sample counts that do not divide evenly are
 * spread so the bars cover the whole signal; a bar with no samples is 0.
 *
 * An all-silent (or empty) signal returns zeros rather than a division by zero.
 */
export function bucketPeaks(samples: Float32Array, buckets = WAVEFORM_BUCKETS): number[] {
  if (buckets <= 0) return []
  const peaks = new Array<number>(buckets).fill(0)
  const total = samples.length
  if (total === 0) return peaks

  for (let bar = 0; bar < buckets; bar++) {
    const start = Math.floor((bar * total) / buckets)
    const end = Math.max(start + 1, Math.floor(((bar + 1) * total) / buckets))
    let peak = 0
    for (let i = start; i < end && i < total; i++) {
      const magnitude = Math.abs(samples[i])
      if (magnitude > peak) peak = magnitude
    }
    peaks[bar] = peak
  }

  const loudest = Math.max(...peaks)
  if (loudest <= 0) return peaks
  return peaks.map(peak => peak / loudest)
}

/**
 * Bar heights in percent, with the silence floor applied. Returned as integers
 * so the markup is stable across renders of the same audio.
 */
export function barHeights(peaks: number[]): number[] {
  return peaks.map(peak => {
    const fraction = Math.min(1, Math.max(0, peak))
    const floored = WAVEFORM_MIN_FRACTION + fraction * (1 - WAVEFORM_MIN_FRACTION)
    return Math.round(floored * 100)
  })
}

/** Fraction of the strip already played, clamped to 0..1. */
export function playedFraction(currentTime: number, duration: number): number {
  if (!Number.isFinite(currentTime) || !Number.isFinite(duration) || duration <= 0) return 0
  return Math.min(1, Math.max(0, currentTime / duration))
}
