import { describe, it, expect } from 'vitest'
import {
  normalizeManifest,
  tierTimeline,
  segmentAt,
  segStartMs,
  segEndMs,
  wordStartMs,
  positionOf,
  durationsOf,
} from '$lib/utils/manifest-timeline'
import type { ManifestSegment, RenderManifest } from '$lib/api'

// Factors a real 29 h render measured; the old tier set included.
const FACTORS: Record<string, number> = {
  '1.0': 1.0, '0.75': 0.7500001515521114, '1.25': 1.2500001452431144, '1.5': 1.5000000248152556,
  '1.75': 1.7500001854154703, '2.0': 1.9999999874080308, '2.5': 2.5000009354763852, '3.0': 3.000000751547916,
}

function base(n = 300): ManifestSegment[] {
  let seed = 11
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  const out: ManifestSegment[] = []
  let pos = 0
  for (let i = 0; i < n; i++) {
    const dur = 400 + rnd() * 5000
    const nw = Math.floor(rnd() * 8)
    const words = Array.from({ length: nw }, (_, k) => ({
      start_ms: pos + (dur * k) / nw, end_ms: pos + (dur * (k + 1)) / nw - rnd() * 3,
      box: [rnd() * 500, rnd() * 700, rnd() * 500, rnd() * 700] as any, timing_source: 'aligned',
    }))
    const gap = [0, 120, 250][i % 3]
    out.push({ index: i * 3 + (i % 3), start_ms: pos, end_ms: pos + dur, gap_ms: gap, words })
    pos += dur + gap
  }
  return out
}

/** Exactly what the backend wrote per tier in schema 1: 1x / actual_factor. */
const divide = (segs: ManifestSegment[], a: number): ManifestSegment[] =>
  segs.map(s => ({ ...s, start_ms: s.start_ms / a, end_ms: s.end_ms / a,
    words: s.words.map(w => ({ ...w, start_ms: w.start_ms / a, end_ms: w.end_ms / a })) }))

const meta = { duration_ms: 1, base_effective_speed: 1, voice: 'v', fingerprint: 'f', container: 'ogg-opus',
  timing_quality: 'aligned' as const, mixed_backends: false }

function slim(segs = base()): RenderManifest {
  return { ...meta, schema: 2, segments: segs,
    tiers: Object.fromEntries(Object.entries(FACTORS).map(([k, a]) =>
      [k, { requested_factor: Number(k), actual_factor: a, max_drift_ms: 1 }])) } as RenderManifest
}

function v1(segs = base()): any {
  return { ...meta, schema: 1,
    tiers: Object.fromEntries(Object.entries(FACTORS).map(([k, a]) =>
      [k, { requested_factor: Number(k), actual_factor: a, max_drift_ms: 1, segments: divide(segs, a) }])) }
}

function linearAt(segs: ManifestSegment[], ms: number): number {
  let f = -1
  for (let i = 0; i < segs.length; i++) if (segs[i].start_ms <= ms) f = i
  return f
}

describe('manifest timeline (slim: one 1x timeline, tiers divide)', () => {
  it('every tier/segment/word equals the schema 1 value bit for bit', () => {
    const b = base()
    const m = slim(b)
    for (const [k, a] of Object.entries(FACTORS)) {
      const want = divide(b, a)
      const tl = tierTimeline(m, k)!
      for (let i = 0; i < b.length; i++) {
        expect(segStartMs(tl, i)).toBe(want[i].start_ms)
        expect(segEndMs(tl, i)).toBe(want[i].end_ms)
        for (let w = 0; w < b[i].words.length; w++) expect(wordStartMs(tl, i, w)).toBe(want[i].words[w].start_ms)
      }
    }
  })

  it('segmentAt matches a linear scan of the schema 1 tier, including exact boundaries', () => {
    const b = base()
    const m = slim(b)
    for (const [k, a] of Object.entries(FACTORS)) {
      const want = divide(b, a)
      const tl = tierTimeline(m, k)!
      const probes = [-1, 0, ...want.flatMap(s => [s.start_ms, s.start_ms - 1e-9, (s.start_ms + s.end_ms) / 2, s.end_ms])]
      for (const ms of probes) expect(segmentAt(tl, ms)).toBe(linearAt(want, ms))
    }
  })

  it('positionOf finds a sentence index without scanning, -1 when absent', () => {
    const b = base()
    const tl = tierTimeline(slim(b), '2.0')!
    for (let i = 0; i < b.length; i += 7) expect(positionOf(tl, b[i].index)).toBe(i)
    expect(positionOf(tl, 2)).toBe(-1)
  })

  it('durations per sentence equal the schema 1 tier', () => {
    const b = base()
    const want = divide(b, FACTORS['1.5'])
    const d = durationsOf(tierTimeline(slim(b), '1.5')!)
    for (const s of want) expect(d[s.index]).toBe((s.end_ms - s.start_ms) / 1000)
  })

  it('a schema 1 manifest normalizes to embedded per-tier timelines with no copying', () => {
    const raw = v1()
    const m = normalizeManifest(raw)
    expect(m.segments).toBe(raw.tiers['1.0'].segments)
    for (const k of Object.keys(FACTORS)) {
      const tl = tierTimeline(m, k)!
      expect(tl.segs).toBe(raw.tiers[k].segments)
      expect(tl.div).toBe(1)
    }
  })

  it('a slim tier that carries its own segments (an odd old render) uses them', () => {
    const b = base(20)
    const m = slim(b)
    const own = divide(b, 1.3)
    m.tiers['1.25'] = { ...m.tiers['1.25'], segments: own }
    const tl = tierTimeline(m, '1.25')!
    expect(tl.segs).toBe(own)
    expect(segStartMs(tl, 5)).toBe(own[5].start_ms)
  })

  it('normalize keeps a slim manifest as is and tolerates missing pieces', () => {
    const m = slim()
    expect(normalizeManifest(m)).toBe(m)
    const bare = normalizeManifest({ voice: 'v', tiers: {} } as never)
    expect(bare.segments).toEqual([])
    expect(tierTimeline(bare, '1.0')).toBeNull()
  })
})
