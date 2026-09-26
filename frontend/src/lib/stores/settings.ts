import { writable } from 'svelte/store'
import { getUserSettings, updateUserSettings } from '$lib/api'

const STORAGE_KEY = 'kokoro-settings'

export type Theme = 'light' | 'sepia' | 'dark' | 'system'

// Highlight swatches offered in Settings (DESIGN.md §1). Legacy stored
// values outside this list still load — isValidColor accepts any #rrggbb.
export const HIGHLIGHT_SWATCHES = [
  '#FCD34D', // Honey (default)
  '#C4B5FD', // Lavender
  '#6EE7B7', // Mint
  '#F9A8D4', // Rose
  '#7DD3FC', // Sky
  '#FCA5A5', // Coral
  '#FDBA74', // Peach
  '#67E8F9', // Aqua
] as const

const VALID_COLORS = new Set<string>(HIGHLIGHT_SWATCHES)

export interface SettingsState {
  voice: string
  highlightColor: string
  autoscroll: boolean
  hotkeysEnabled: boolean
  bionicMode: boolean
  bionicFixation: number
  bionicBoldRatio: number
  highlightEnabled: boolean
  theme: Theme
}

const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#FCD34D',
  autoscroll: true,
  hotkeysEnabled: true,
  bionicMode: false,
  bionicFixation: 1,
  bionicBoldRatio: 0.5,
  highlightEnabled: true,
  theme: 'system',
}

function isValidColor(c: string): boolean {
  return VALID_COLORS.has(c) || /^#[0-9a-fA-F]{6}$/.test(c)
}

function isValidTheme(t: unknown): t is Theme {
  return t === 'light' || t === 'sepia' || t === 'dark' || t === 'system'
}

function resolveTheme(theme: Theme): 'light' | 'sepia' | 'dark' {
  if (theme !== 'system') return theme
  if (typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = resolveTheme(theme)
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
      highlightEnabled: typeof parsed.highlightEnabled === 'boolean' ? parsed.highlightEnabled : DEFAULTS.highlightEnabled,
      theme: isValidTheme(parsed.theme) ? parsed.theme : DEFAULTS.theme,
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

  // Keep <html data-theme> in sync with the stored preference. Guarded for
  // jsdom (no matchMedia) and SSR (no document/window).
  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    let currentTheme: Theme = 'system'
    subscribe(s => {
      currentTheme = s.theme
      applyTheme(s.theme)
    })
    if (typeof matchMedia === 'function') {
      matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (currentTheme === 'system') applyTheme('system')
      })
    }
  }

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

    setTheme(theme: Theme) {
      if (!isValidTheme(theme)) return
      update(s => ({ ...s, theme }))
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

    toggleHighlight() {
      let next = true
      update(s => { next = !s.highlightEnabled; return { ...s, highlightEnabled: next } })
      updateUserSettings({ highlight_enabled: next }).catch(() => {})
    },

    async loadFromServer() {
      try {
        const serverSettings = await getUserSettings()
        update(s => ({ ...s, highlightEnabled: serverSettings.highlight_enabled ?? true }))
      } catch {
        // Server unavailable — keep local value
      }
    },

    reset() {
      set({ ...DEFAULTS })
    },
  }
}

export const settingsStore = createSettingsStore()
