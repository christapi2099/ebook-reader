import { writable } from 'svelte/store'
import { getUserSettings, updateUserSettings } from '$lib/api'

const STORAGE_KEY = 'kokoro-settings'

export type Theme = 'light' | 'sepia' | 'dark' | 'system'

// Highlight swatches offered in Settings (DESIGN.md §1). Legacy stored
// values outside this list still load — isValidColor accepts any #rrggbb.
export const HIGHLIGHT_SWATCHES = [
  '#F5CB5C', // Amber (default)
  '#A8D672', // Leaf
  '#7EC8E3', // Sky
  '#F0A6CA', // Rose
  '#B8A6F0', // Violet
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
  /** Words shorter than this are left unbolded (`BionicOptions.minWordLength`). */
  bionicMinWordLength: number
  /** Leave the `COMMON_WORDS` set unbolded (`BionicOptions.skipCommonWords`). */
  bionicSkipCommonWords: boolean
  highlightEnabled: boolean
  /** Sentence highlight opacity, 0.1 to 1. `null` keeps each theme's own `--highlight-alpha`. */
  highlightStrength: number | null
  theme: Theme
}

/** Bounds of the highlight strength slider (an opacity). */
export const HIGHLIGHT_STRENGTH_RANGE = { min: 0.1, max: 1 } as const
/** The word highlight sits this much above the sentence highlight, capped at 1. */
const WORD_ALPHA_BOOST = 0.35

/** Bounds of the minimum-word-length slider, in characters. */
export const BIONIC_MIN_WORD_LENGTH_RANGE = { min: 1, max: 8 } as const

const DEFAULTS: SettingsState = {
  voice: 'af_heart',
  highlightColor: '#F5CB5C',
  autoscroll: true,
  hotkeysEnabled: true,
  bionicMode: false,
  bionicFixation: 1,
  bionicBoldRatio: 0.5,
  // Match `DEFAULT_BIONIC_OPTIONS`, so an untouched install renders exactly what
  // the algorithm's own defaults describe.
  bionicMinWordLength: 3,
  bionicSkipCommonWords: true,
  highlightEnabled: true,
  highlightStrength: null,
  theme: 'system',
}

const KOKORO_VOICE_ID = /^(af|am|bf|bm)_/
const DEFAULT_KOKORO_VOICE = 'af_heart'

/**
 * The voice setting is stored apart from the engine, so it can name a voice the
 * live engine cannot speak. Map it onto the live engine's list: a Kokoro id while
 * Piper is live becomes the first Piper voice, and a Piper id while Piper is not
 * live falls back to the Kokoro default. Anything else is left untouched, so a
 * Kokoro user's stored voice never changes.
 */
export function reconcileVoiceForEngine(voice: string, voices: { id: string }[]): string {
  const piper = voices.find(v => v.id.startsWith('piper:'))
  if (KOKORO_VOICE_ID.test(voice) && piper) return piper.id
  if (voice.startsWith('piper:') && !piper) return DEFAULT_KOKORO_VOICE
  return voice
}

function isValidColor(c: string): boolean {
  return VALID_COLORS.has(c) || /^#[0-9a-fA-F]{6}$/.test(c)
}

function isValidTheme(t: unknown): t is Theme {
  return t === 'light' || t === 'sepia' || t === 'dark' || t === 'system'
}

function isMinWordLength(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= BIONIC_MIN_WORD_LENGTH_RANGE.min &&
    value <= BIONIC_MIN_WORD_LENGTH_RANGE.max
  )
}

function resolveTheme(theme: Theme): 'light' | 'sepia' | 'dark' {
  if (theme !== 'system') return theme
  if (typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function isStrength(v: unknown): v is number {
  return typeof v === 'number' && v >= HIGHLIGHT_STRENGTH_RANGE.min && v <= HIGHLIGHT_STRENGTH_RANGE.max
}

/** Inline vars on <html> win over the per-theme values in app.css; removing them restores the theme. */
function applyHighlightStrength(strength: number | null): void {
  if (typeof document === 'undefined') return
  const style = document.documentElement.style
  if (strength === null) {
    style.removeProperty('--highlight-alpha')
    style.removeProperty('--word-alpha')
    return
  }
  style.setProperty('--highlight-alpha', String(strength))
  style.setProperty('--word-alpha', String(Math.min(1, strength + WORD_ALPHA_BOOST)))
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
      bionicMinWordLength: isMinWordLength(parsed.bionicMinWordLength)
        ? parsed.bionicMinWordLength
        : DEFAULTS.bionicMinWordLength,
      bionicSkipCommonWords: typeof parsed.bionicSkipCommonWords === 'boolean'
        ? parsed.bionicSkipCommonWords
        : DEFAULTS.bionicSkipCommonWords,
      highlightEnabled: typeof parsed.highlightEnabled === 'boolean' ? parsed.highlightEnabled : DEFAULTS.highlightEnabled,
      highlightStrength: parsed.highlightStrength === null || isStrength(parsed.highlightStrength)
        ? (parsed.highlightStrength ?? null)
        : DEFAULTS.highlightStrength,
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
      applyHighlightStrength(s.highlightStrength)
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

    /** An opacity in [0.1, 1]; `null` goes back to the theme's own strength. */
    setHighlightStrength(value: number | null) {
      if (value !== null && !Number.isFinite(value)) return
      const next = value === null
        ? null
        : Math.max(HIGHLIGHT_STRENGTH_RANGE.min, Math.min(HIGHLIGHT_STRENGTH_RANGE.max, Math.round(value * 20) / 20))
      update(s => ({ ...s, highlightStrength: next }))
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

    setBionicMinWordLength(value: number) {
      if (!Number.isFinite(value)) return
      const clamped = Math.max(
        BIONIC_MIN_WORD_LENGTH_RANGE.min,
        Math.min(BIONIC_MIN_WORD_LENGTH_RANGE.max, Math.round(value)),
      )
      update(s => ({ ...s, bionicMinWordLength: clamped }))
    },

    toggleBionicSkipCommonWords() {
      update(s => ({ ...s, bionicSkipCommonWords: !s.bionicSkipCommonWords }))
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
