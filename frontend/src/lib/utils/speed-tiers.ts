/**
 * Playback speeds are the render's pre-built tiers, so the speeds a reader can
 * pick are exactly the keys of `manifest.tiers` ("1.0", "1.5", ...). Newer renders
 * carry 1, 1.5, 2, 2.5 and 3; older ones also 0.75, 1.25 and 1.75.
 */

/** Sorted numeric speeds of a manifest's tier map; unparseable keys are skipped. */
export function manifestSpeeds(tiers: Record<string, unknown>): number[] {
  return Object.keys(tiers)
    .map(k => Number.parseFloat(k))
    .filter(f => Number.isFinite(f) && f > 0)
    .sort((a, b) => a - b)
}

/**
 * The available speed closest to `speed`; ties go to the slower tier. With no
 * speeds (nothing loaded yet) the speed is returned unchanged.
 */
export function nearestSpeed(speeds: readonly number[], speed: number): number {
  let best: number | null = null
  for (const s of speeds) {
    if (best === null || Math.abs(s - speed) < Math.abs(best - speed) - 1e-9) best = s
  }
  return best ?? speed
}

/** The next available speed above (dir 1) or below (dir -1) `speed`, clamped at the ends. */
export function stepSpeed(speeds: readonly number[], speed: number, dir: 1 | -1): number {
  if (speeds.length === 0) return speed
  if (dir > 0) return speeds.find(s => s > speed + 1e-9) ?? speeds[speeds.length - 1]
  for (let i = speeds.length - 1; i >= 0; i--) if (speeds[i] < speed - 1e-9) return speeds[i]
  return speeds[0]
}
