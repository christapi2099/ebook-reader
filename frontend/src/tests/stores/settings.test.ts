import { describe, it, expect, beforeEach, vi } from 'vitest'
import { settingsStore, BIONIC_MIN_WORD_LENGTH_RANGE, type SettingsState } from '$lib/stores/settings'
import { DEFAULT_BIONIC_OPTIONS } from '$lib/utils/bionic-reading'
import { get } from 'svelte/store'

// Mirrors the store's DEFAULTS. Updated for the design-system revamp:
// default highlight is now Honey #FCD34D and a `theme` setting was added.
const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#FCD34D',
  autoscroll: true,
  hotkeysEnabled: true,
  highlightEnabled: true,
  bionicMode: false,
  bionicFixation: 1,
  bionicBoldRatio: 0.5,
  bionicMinWordLength: 3,
  bionicSkipCommonWords: true,
  theme: 'system',
}

describe('settingsStore', () => {
  beforeEach(() => {
    localStorage.clear()
    settingsStore.reset()
  })

  it('has correct default values', () => {
    const state = get(settingsStore)
    expect(state).toEqual(DEFAULTS)
  })

  it('persists to localStorage on change', () => {
    settingsStore.setVoice('af_bella')
    const saved = JSON.parse(localStorage.getItem('kokoro-settings') || '{}')
    expect(saved.voice).toBe('af_bella')
  })

  it('loads from localStorage on init', () => {
    localStorage.setItem('kokoro-settings', JSON.stringify({ ...DEFAULTS, voice: 'am_adam' }))
    settingsStore.reloadFromStorage()
    expect(get(settingsStore).voice).toBe('am_adam')
  })

  it('setVoice updates the voice', () => {
    settingsStore.setVoice('bf_emma')
    expect(get(settingsStore).voice).toBe('bf_emma')
  })

  it('setHighlightColor updates the color', () => {
    settingsStore.setHighlightColor('#86efac')
    expect(get(settingsStore).highlightColor).toBe('#86efac')
  })

  it('toggleAutoscroll flips autoscroll', () => {
    expect(get(settingsStore).autoscroll).toBe(true)
    settingsStore.toggleAutoscroll()
    expect(get(settingsStore).autoscroll).toBe(false)
    settingsStore.toggleAutoscroll()
    expect(get(settingsStore).autoscroll).toBe(true)
  })

  it('toggleHotkeys flips hotkeysEnabled', () => {
    expect(get(settingsStore).hotkeysEnabled).toBe(true)
    settingsStore.toggleHotkeys()
    expect(get(settingsStore).hotkeysEnabled).toBe(false)
  })

  it('setHighlightColor rejects invalid colors and uses fallback', () => {
    settingsStore.setHighlightColor('not-a-color')
    expect(get(settingsStore).highlightColor).toBe(DEFAULTS.highlightColor)
  })

  it('reset returns all values to defaults', () => {
    settingsStore.setVoice('am_michael')
    settingsStore.setHighlightColor('#86efac')
    settingsStore.toggleAutoscroll()
    settingsStore.toggleHotkeys()
    settingsStore.toggleBionicMode()
    settingsStore.setBionicFixation(3)
    settingsStore.setBionicBoldRatio(0.7)
    settingsStore.reset()
    expect(get(settingsStore)).toEqual(DEFAULTS)
  })

  describe('bionic reading settings', () => {
    it('has bionicMode defaulting to false', () => {
      expect(get(settingsStore).bionicMode).toBe(false)
    })

    it('has bionicFixation defaulting to 1', () => {
      expect(get(settingsStore).bionicFixation).toBe(1)
    })

    it('has bionicBoldRatio defaulting to 0.5', () => {
      expect(get(settingsStore).bionicBoldRatio).toBe(0.5)
    })

    it('toggleBionicMode flips bionicMode', () => {
      settingsStore.toggleBionicMode()
      expect(get(settingsStore).bionicMode).toBe(true)
      settingsStore.toggleBionicMode()
      expect(get(settingsStore).bionicMode).toBe(false)
    })

    it('setBionicFixation clamps to [1, 5]', () => {
      settingsStore.setBionicFixation(7)
      expect(get(settingsStore).bionicFixation).toBe(5)
      settingsStore.setBionicFixation(0)
      expect(get(settingsStore).bionicFixation).toBe(1)
    })

    it('setBionicFixation accepts valid values within range', () => {
      settingsStore.setBionicFixation(3)
      expect(get(settingsStore).bionicFixation).toBe(3)
      settingsStore.setBionicFixation(1)
      expect(get(settingsStore).bionicFixation).toBe(1)
      settingsStore.setBionicFixation(5)
      expect(get(settingsStore).bionicFixation).toBe(5)
    })

    it('setBionicBoldRatio clamps to [0.2, 0.8]', () => {
      settingsStore.setBionicBoldRatio(0.1)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.2)
      settingsStore.setBionicBoldRatio(0.9)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.8)
    })

    it('setBionicBoldRatio accepts valid values within range', () => {
      settingsStore.setBionicBoldRatio(0.35)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.35)
      settingsStore.setBionicBoldRatio(0.5)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.5)
      settingsStore.setBionicBoldRatio(0.8)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.8)
    })

    it('setBionicBoldRatio rounds to nearest 0.05', () => {
      settingsStore.setBionicBoldRatio(0.53)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.55)
      settingsStore.setBionicBoldRatio(0.52)
      expect(get(settingsStore).bionicBoldRatio).toBe(0.5)
    })

    it('setBionicFixation ignores NaN', () => {
      const before = get(settingsStore).bionicFixation
      settingsStore.setBionicFixation(NaN)
      expect(get(settingsStore).bionicFixation).toBe(before)
    })

    it('setBionicBoldRatio ignores NaN', () => {
      const before = get(settingsStore).bionicBoldRatio
      settingsStore.setBionicBoldRatio(NaN)
      expect(get(settingsStore).bionicBoldRatio).toBe(before)
    })

    it('persists bionic settings to localStorage', () => {
      settingsStore.toggleBionicMode()
      settingsStore.setBionicFixation(4)
      settingsStore.setBionicBoldRatio(0.35)
      const saved = JSON.parse(localStorage.getItem('kokoro-settings') || '{}')
      expect(saved.bionicMode).toBe(true)
      expect(saved.bionicFixation).toBe(4)
      expect(saved.bionicBoldRatio).toBe(0.35)
    })

    it('loads bionic settings from localStorage', () => {
      localStorage.setItem('kokoro-settings', JSON.stringify({ ...DEFAULTS, bionicMode: true, bionicFixation: 3 }))
      settingsStore.reloadFromStorage()
      const state = get(settingsStore)
      expect(state.bionicMode).toBe(true)
      expect(state.bionicFixation).toBe(3)
    })

    it('falls back to defaults for corrupted bionic settings', () => {
      localStorage.setItem('kokoro-settings', JSON.stringify({ bionicMode: 'nope', bionicFixation: 'abc', bionicBoldRatio: 'xyz' }))
      settingsStore.reloadFromStorage()
      const state = get(settingsStore)
      expect(state.bionicMode).toBe(false)
      expect(state.bionicFixation).toBe(1)
      expect(state.bionicBoldRatio).toBe(0.5)
    })

    it('has bionicMinWordLength defaulting to the algorithm default of 3', () => {
      expect(get(settingsStore).bionicMinWordLength).toBe(DEFAULT_BIONIC_OPTIONS.minWordLength)
    })

    it('has bionicSkipCommonWords defaulting to the algorithm default', () => {
      expect(get(settingsStore).bionicSkipCommonWords).toBe(DEFAULT_BIONIC_OPTIONS.skipCommonWords)
    })

    it('setBionicMinWordLength clamps to the slider range', () => {
      settingsStore.setBionicMinWordLength(0)
      expect(get(settingsStore).bionicMinWordLength).toBe(BIONIC_MIN_WORD_LENGTH_RANGE.min)
      settingsStore.setBionicMinWordLength(99)
      expect(get(settingsStore).bionicMinWordLength).toBe(BIONIC_MIN_WORD_LENGTH_RANGE.max)
      settingsStore.setBionicMinWordLength(5)
      expect(get(settingsStore).bionicMinWordLength).toBe(5)
    })

    it('setBionicMinWordLength rounds and ignores NaN', () => {
      settingsStore.setBionicMinWordLength(4.6)
      expect(get(settingsStore).bionicMinWordLength).toBe(5)
      settingsStore.setBionicMinWordLength(NaN)
      expect(get(settingsStore).bionicMinWordLength).toBe(5)
    })

    it('toggleBionicSkipCommonWords flips the option', () => {
      settingsStore.toggleBionicSkipCommonWords()
      expect(get(settingsStore).bionicSkipCommonWords).toBe(false)
      settingsStore.toggleBionicSkipCommonWords()
      expect(get(settingsStore).bionicSkipCommonWords).toBe(true)
    })

    it('persists the two extra bionic options to localStorage', () => {
      settingsStore.setBionicMinWordLength(6)
      settingsStore.toggleBionicSkipCommonWords()
      const saved = JSON.parse(localStorage.getItem('kokoro-settings') || '{}')
      expect(saved.bionicMinWordLength).toBe(6)
      expect(saved.bionicSkipCommonWords).toBe(false)
    })

    it('loads the two extra bionic options from localStorage', () => {
      localStorage.setItem('kokoro-settings', JSON.stringify({
        ...DEFAULTS,
        bionicMinWordLength: 7,
        bionicSkipCommonWords: false,
      }))
      settingsStore.reloadFromStorage()
      const state = get(settingsStore)
      expect(state.bionicMinWordLength).toBe(7)
      expect(state.bionicSkipCommonWords).toBe(false)
    })

    it('rejects an out-of-range or non-integer stored min word length', () => {
      localStorage.setItem('kokoro-settings', JSON.stringify({ ...DEFAULTS, bionicMinWordLength: 40 }))
      settingsStore.reloadFromStorage()
      expect(get(settingsStore).bionicMinWordLength).toBe(DEFAULTS.bionicMinWordLength)

      localStorage.setItem('kokoro-settings', JSON.stringify({ ...DEFAULTS, bionicMinWordLength: 2.5 }))
      settingsStore.reloadFromStorage()
      expect(get(settingsStore).bionicMinWordLength).toBe(DEFAULTS.bionicMinWordLength)
    })
  })
})
