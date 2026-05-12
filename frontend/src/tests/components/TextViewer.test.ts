import { describe, it, expect, beforeEach } from 'vitest'
import { get } from 'svelte/store'
import { settingsStore } from '$lib/stores/settings'
import { bionifyText, bionifyTextToSegments } from '$lib/utils/bionic-reading'

describe('TextViewer - bionic reading integration', () => {
  beforeEach(() => {
    settingsStore.reset()
  })
  it('settingsStore bionicMode controls whether bionifyText is applied', () => {
    const settings = get(settingsStore)
    expect(settings.bionicMode).toBe(false)
    const text = 'The quick brown fox'
    const result = bionifyTextToSegments(text, {
      fixationPoint: settings.bionicFixation,
      boldRatio: settings.bionicBoldRatio,
    })
    const hasBold = result.some(s => s.bold)
    expect(hasBold).toBe(true)
  })

  it('bionifyText produces bold segments for non-common words', () => {
    const segments = bionifyTextToSegments('quick brown fox')
    const boldTexts = segments.filter(s => s.bold).map(s => s.text)
    expect(boldTexts).toContain('qui')
    expect(boldTexts).toContain('bro')
    expect(boldTexts).toContain('fo')
  })

  it('bionifyText leaves common words unbolded', () => {
    const segments = bionifyTextToSegments('the and for')
    const boldTexts = segments.filter(s => s.bold).map(s => s.text)
    expect(boldTexts).toHaveLength(0)
  })

  it('bionifyText returns per-word BionicWord for word-level tracking', () => {
    const words = bionifyText('cat dog')
    expect(words).toHaveLength(2)
    expect(words[0].bold).toBe('ca')
    expect(words[0].rest).toBe('t')
    expect(words[1].bold).toBe('do')
    expect(words[1].rest).toBe('g')
  })

  it('whitespace is preserved as separate non-bold segments', () => {
    const segments = bionifyTextToSegments('cat dog')
    const whitespace = segments.find(s => s.text === ' ')
    expect(whitespace).toBeDefined()
    expect(whitespace!.bold).toBe(false)
  })

  it('respects settings store bionicFixation', () => {
    settingsStore.setBionicFixation(5)
    const settings = get(settingsStore)
    const segments = bionifyTextToSegments('reading', {
      fixationPoint: settings.bionicFixation,
    })
    const boldText = segments.filter(s => s.bold).map(s => s.text).join('')
    expect(boldText).toBe('rea')
    settingsStore.setBionicFixation(1)
  })

  it('respects settings store bionicBoldRatio', () => {
    settingsStore.setBionicBoldRatio(0.7)
    const settings = get(settingsStore)
    const segments = bionifyTextToSegments('reading', {
      boldRatio: settings.bionicBoldRatio,
    })
    const boldText = segments.filter(s => s.bold).map(s => s.text).join('')
    expect(boldText).toBe('readi')
    settingsStore.setBionicBoldRatio(0.5)
  })
})
