import { describe, it, expect, beforeEach, vi } from 'vitest'
import { settingsStore, type SettingsState } from '$lib/stores/settings'
import { get } from 'svelte/store'

const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#fef08a',
  autoscroll: true,
  hotkeysEnabled: true,
}

describe('settingsStore', () => {
  beforeEach(() => {
    localStorage.clear()
    // Reinitialize by calling the store's reset
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
    settingsStore.reset()
    expect(get(settingsStore)).toEqual(DEFAULTS)
  })
})
