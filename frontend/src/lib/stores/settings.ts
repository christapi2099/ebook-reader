import { writable } from 'svelte/store'

const STORAGE_KEY = 'kokoro-settings'

const VALID_COLORS = new Set([
  '#fef08a',
  '#86efac',
  '#93c5fd',
  '#fdba74',
  '#f9a8d4',
  '#c4b5fd',
  '#fca5a5',
  '#67e8f9',
])

export interface SettingsState {
  voice: string
  highlightColor: string
  autoscroll: boolean
  hotkeysEnabled: boolean
}

const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#fef08a',
  autoscroll: true,
  hotkeysEnabled: true,
}

function isValidColor(c: string): boolean {
  return VALID_COLORS.has(c) || /^#[0-9a-fA-F]{6}$/.test(c)
}

function readFromStorage(): SettingsState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw)
    return {
      voice: typeof parsed.voice === 'string' ? parsed.voice : DEFAULTS.voice,
      highlightColor: isValidColor(parsed.highlightColor) ? parsed.highlightColor : DEFAULTS.highlightColor,
      autoscroll: typeof parsed.autoscroll === 'boolean' ? parsed.autoscroll : DEFAULTS.autoscroll,
      hotkeysEnabled: typeof parsed.hotkeysEnabled === 'boolean' ? parsed.hotkeysEnabled : DEFAULTS.hotkeysEnabled,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

function saveToStorage(state: SettingsState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Storage quota exceeded or unavailable
  }
}

function createSettingsStore() {
  const { subscribe, set, update } = writable<SettingsState>(readFromStorage())

  subscribe(saveToStorage)

  return {
    subscribe,

    reloadFromStorage() {
      set(readFromStorage())
    },

    setVoice(voice: string) {
      update(s => ({ ...s, voice }))
    },

    setHighlightColor(color: string) {
      if (!isValidColor(color)) return
      update(s => ({ ...s, highlightColor: color }))
    },

    toggleAutoscroll() {
      update(s => ({ ...s, autoscroll: !s.autoscroll }))
    },

    toggleHotkeys() {
      update(s => ({ ...s, hotkeysEnabled: !s.hotkeysEnabled }))
    },

    reset() {
      set({ ...DEFAULTS })
    },
  }
}

export const settingsStore = createSettingsStore()
