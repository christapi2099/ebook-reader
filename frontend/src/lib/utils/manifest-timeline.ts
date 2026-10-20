/**
 * Tier timelines from the slim manifest (schema 2).
 *
 * The backend stores the 1x timeline once (`manifest.segments`); a tier's times
 * are `1x time / actual_factor`, the same division the encoder used, so every
 * value here is bit-identical to what a schema 1 manifest repeated per tier. A
 * tier that carries its own `segments` (an old render whose tier was not that
 * division) is used as is, with no division. A schema 1 manifest normalizes to
 * exactly that case, without copying anything.
 *
 * Nothing here copies or wraps the arrays: a 29 h book has 18.5k segments and
 * 246k words, so callers keep them as plain (non-reactive) data.
 */
import type { ManifestSegment, RenderManifest } from '$lib/api'

export interface Timeline {
  /** The segments to read: the 1x timeline, or a tier's own. */
  segs: ManifestSegment[]
  /** Divide every time by this: the tier's actual_factor, or 1 for its own segments. */
  div: number
}

/** A schema 2 manifest is returned as is; a schema 1 one gets `segments` = its 1x tier's. */
export function normalizeManifest(m: RenderManifest): RenderManifest {
  if (Array.isArray(m.segments)) return m
  const tiers = m.tiers ?? {}
  const one = tiers['1.0']?.segments ?? Object.values(tiers).find(t => t?.actual_factor === 1 && t.segments)?.segments
  return { ...m, tiers, segments: one ?? [] }
}

export function tierTimeline(m: RenderManifest, key: string): Timeline | null {
  const t = m.tiers?.[key]
  if (!t) return null
  if (Array.isArray(t.segments)) return { segs: t.segments, div: 1 }
  const a = t.actual_factor
  if (!(a > 0) || !Array.isArray(m.segments)) return null
  return { segs: m.segments, div: a }
}

export const segStartMs = (tl: Timeline, i: number): number => tl.segs[i].start_ms / tl.div
export const segEndMs = (tl: Timeline, i: number): number => tl.segs[i].end_ms / tl.div
export const wordStartMs = (tl: Timeline, i: number, w: number): number => tl.segs[i].words[w].start_ms / tl.div

/** Last segment position whose start <= ms (binary search); -1 before the first. */
export function segmentAt(tl: Timeline, ms: number): number {
  const { segs, div } = tl
  let lo = 0
  let hi = segs.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (segs[mid].start_ms / div <= ms) { found = mid; lo = mid + 1 } else hi = mid - 1
  }
  return found
}

/** Last word position in segment `i` whose start <= ms; -1 before the first. */
export function wordAt(tl: Timeline, i: number, ms: number): number {
  const words = tl.segs[i].words
  let lo = 0
  let hi = words.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (words[mid].start_ms / tl.div <= ms) { found = mid; lo = mid + 1 } else hi = mid - 1
  }
  return found
}

const positions = new WeakMap<ManifestSegment[], Map<number, number>>()

/** Position of the segment for sentence `index`, -1 when it has none. O(1) after the first call. */
export function positionOf(tl: Timeline, index: number): number {
  let map = positions.get(tl.segs)
  if (!map) {
    map = new Map()
    for (let i = 0; i < tl.segs.length; i++) map.set(tl.segs[i].index, i)
    positions.set(tl.segs, map)
  }
  return map.get(index) ?? -1
}

/** Seconds per sentence index on this tier: what the progress bar sums. */
export function durationsOf(tl: Timeline): Record<number, number> {
  const out: Record<number, number> = {}
  for (let i = 0; i < tl.segs.length; i++) out[tl.segs[i].index] = (segEndMs(tl, i) - segStartMs(tl, i)) / 1000
  return out
}
