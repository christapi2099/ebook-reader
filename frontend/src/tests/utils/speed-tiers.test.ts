import { describe, it, expect } from 'vitest'
import { nearestSpeed, stepSpeed, manifestSpeeds } from '$lib/utils/speed-tiers'

const NEW = [1, 1.5, 2, 2.5, 3]
const OLD = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3]

describe('speed tiers', () => {
  it('reads the sorted speeds out of a manifest tier map', () => {
    expect(manifestSpeeds({ '2.0': {}, '1.0': {}, '1.5': {}, 'x': {} })).toEqual([1, 1.5, 2])
  })

  it('snaps a speed the render does not have to the nearest tier', () => {
    expect(nearestSpeed(NEW, 1.5)).toBe(1.5)
    expect(nearestSpeed(NEW, 0.75)).toBe(1)
    expect(nearestSpeed(NEW, 0.5)).toBe(1)
    expect(nearestSpeed(NEW, 2.2)).toBe(2)
    expect(nearestSpeed(NEW, 2.8)).toBe(3)
    // Ties go to the slower tier: a listener who chose 1.25x is not pushed faster.
    expect(nearestSpeed(NEW, 1.25)).toBe(1)
    expect(nearestSpeed(NEW, 1.75)).toBe(1.5)
    expect(nearestSpeed(OLD, 1.25)).toBe(1.25)
  })

  it('leaves the speed alone when nothing is loaded', () => {
    expect(nearestSpeed([], 1.25)).toBe(1.25)
  })

  it('steps to the next tier up or down and stops at the ends', () => {
    expect(stepSpeed(NEW, 1, 1)).toBe(1.5)
    expect(stepSpeed(NEW, 1.5, -1)).toBe(1)
    expect(stepSpeed(NEW, 3, 1)).toBe(3)
    expect(stepSpeed(NEW, 1, -1)).toBe(1)
    expect(stepSpeed(NEW, 1.25, 1)).toBe(1.5) // off-tier speed: first tier above
    expect(stepSpeed(OLD, 1, 1)).toBe(1.25)
    expect(stepSpeed([], 1, 1)).toBe(1)
  })
})
