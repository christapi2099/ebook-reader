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
  bionicMode: boolean
  bionicFixation: number
  bionicBoldRatio: number
}

const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#fef08a',
  autoscroll: true,
  hotkeysEnabled: true,
  bionicMode: false,
  bionicFixation: 1,
  bionicBoldRatio: 0.5,
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
      bionicMode: typeof parsed.bionicMode === 'boolean'
        ? parsed.bionicMode
        : DEFAULTS.bionicMode,
      bionicFixation: typeof parsed.bionicFixation === 'number' && parsed.bionicFixation >= 1 && parsed.bionicFixation <= 5
        ? parsed.bionicFixation
        : DEFAULTS.bionicFixation,
      bionicBoldRatio: typeof parsed.bionicBoldRatio === 'number' && parsed.bionicBoldRatio >= 0.2 && parsed.bionicBoldRatio <= 0.8
        ? parsed.bionicBoldRatio
        : DEFAULTS.bionicBoldRatio,
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

    toggleBionicMode() {
      update(s => ({ ...s, bionicMode: !s.bionicMode }))
    },

    setBionicFixation(value: number) {
      if (!Number.isFinite(value)) return
      const clamped = Math.max(1, Math.min(5, Math.round(value)))
      update(s => ({ ...s, bionicFixation: clamped }))
    },

    setBionicBoldRatio(value: number) {
      if (!Number.isFinite(value)) return
      const clamped = Math.max(0.2, Math.min(0.8, Math.round(value * 20) / 20))
      update(s => ({ ...s, bionicBoldRatio: clamped }))
    },

    reset() {
      set({ ...DEFAULTS })
    },
  }
}

export const settingsStore = createSettingsStore()
