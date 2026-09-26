/**
 * DESIGN.md §2 publishes a *computed* WCAG contrast table. Nothing enforced it.
 *
 * These tests read the real tokens out of `src/app.css`, compute the ratios the
 * WCAG way, and compare them with the numbers the design document claims. So the
 * table can no longer drift from the tokens it describes: change a colour
 * without re-checking contrast and this fails.
 *
 * The `danger` pair is the reason this file exists. `danger` is a light red in
 * the dark theme, and the delete button had been written with a hardcoded
 * `text-white`, which measured 2.79:1 there — an AA failure. `danger-fg` is the
 * fix, and the light-theme value (4.54:1) is only barely above the 4.5 threshold,
 * so it is worth guarding.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'

// `import.meta.url` is not a file: URL under vitest's jsdom runner, so resolve
// against the project root instead — vitest runs with cwd set to `frontend/`.
const CSS = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8')

type Theme = 'light' | 'sepia' | 'dark'

/** The body of one theme's token block, found by its `[data-theme='…']` marker. */
function themeTokens(theme: Theme): Map<string, string> {
  const marker = `[data-theme='${theme}']`
  const at = CSS.indexOf(marker)
  if (at === -1) throw new Error(`app.css has no ${marker} block`)

  const open = CSS.indexOf('{', at)
  const close = CSS.indexOf('}', open)
  const body = CSS.slice(open + 1, close)

  const tokens = new Map<string, string>()
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    tokens.set(name, value.trim())
  }
  return tokens
}

/** sRGB channel to its linear-light value (WCAG 2.1 relative luminance). */
function linearise(channel: number): number {
  const c = channel / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const digits = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map(i => parseInt(digits.slice(i, i + 2), 16))
  return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

function ratioFor(theme: Theme, fg: string, bg: string): number {
  const tokens = themeTokens(theme)
  const fgValue = tokens.get(fg)
  const bgValue = tokens.get(bg)
  if (!fgValue) throw new Error(`${theme} has no --${fg}`)
  if (!bgValue) throw new Error(`${theme} has no --${bg}`)
  return contrast(fgValue, bgValue)
}

const AA = 4.5

// The figures DESIGN.md §2 publishes, per theme: [light, sepia, dark].
const PUBLISHED = {
  'fg|bg': [15.92, 10.6, 16.14],
  'accent-fg|accent': [6.51, 6.58, 6.28],
  'danger-fg|danger': [4.54, 5.82, 6.86],
} satisfies Record<string, [number, number, number]>

const THEMES: Theme[] = ['light', 'sepia', 'dark']

describe('DESIGN.md §2 contrast table matches the real tokens', () => {
  for (const [pair, expected] of Object.entries(PUBLISHED)) {
    const [fg, bg] = pair.split('|')

    it(`${fg} on ${bg} measures what the design document claims`, () => {
      THEMES.forEach((theme, index) => {
        expect(ratioFor(theme, fg, bg)).toBeCloseTo(expected[index], 1)
      })
    })

    it(`${fg} on ${bg} clears AA in every theme`, () => {
      for (const theme of THEMES) {
        expect(ratioFor(theme, fg, bg), `${theme}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(AA)
      }
    })
  }

  it('resolves danger-fg away from white in the dark theme', () => {
    // Not a style preference: white on the dark theme's light red is the AA
    // failure this token was added for, so it must not silently come back.
    expect(themeTokens('dark').get('danger-fg')).not.toMatch(/^#(fff|ffffff)$/i)
    expect(themeTokens('light').get('danger-fg')).toMatch(/^#(fff|ffffff)$/i)
  })

  it('would have failed with the hardcoded white it replaced', () => {
    // Pins the size of the bug, so nobody re-derives it as "barely under".
    const darkDanger = themeTokens('dark').get('danger')!
    expect(contrast(darkDanger, '#ffffff')).toBeLessThan(3)
  })
})
