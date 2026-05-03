import { get } from 'svelte/store'
import { settingsStore } from '$lib/stores/settings'

export interface HotkeyMap {
  [key: string]: () => void
}

const DEFAULT_MAP: HotkeyMap = {}

let activeMap: HotkeyMap = DEFAULT_MAP
let listener: ((e: KeyboardEvent) => void) | null = null

function isEditableTarget(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((el as HTMLElement).isContentEditable) return true
  return false
}

function handler(e: KeyboardEvent) {
  const settings = get(settingsStore)
  if (!settings.hotkeysEnabled) return
  if (isEditableTarget(e.target as Element)) return

  const fn = activeMap[e.key]
  if (fn) {
    e.preventDefault()
    fn()
  }
}

export function registerHotkeys(map: HotkeyMap) {
  activeMap = { ...DEFAULT_MAP, ...map }
  if (!listener) {
    listener = handler
    window.addEventListener('keydown', listener)
  }
}

export function unregisterHotkeys() {
  if (listener) {
    window.removeEventListener('keydown', listener)
    listener = null
  }
  activeMap = DEFAULT_MAP
}

export function updateHotkeys(map: Record<string, () => void>) {
  activeMap = { ...activeMap, ...map }
}
