import { writable } from 'svelte/store'

/** Reader side panels — only one open at a time. */
export type PanelId = 'search' | 'bookmarks' | 'settings' | 'chapters' | null

export interface UiState {
  /** Desktop sidebar collapsed to icon rail (user preference, ≥lg). */
  sidebarCollapsed: boolean
  /** Immersive reading mode: shell chrome hidden, reader fills viewport. */
  immersive: boolean
  activePanel: PanelId
}

function createUiStore() {
  const { subscribe, update } = writable<UiState>({
    sidebarCollapsed: false,
    immersive: false,
    activePanel: null,
  })

  return {
    subscribe,

    toggleSidebar() {
      update(s => ({ ...s, sidebarCollapsed: !s.sidebarCollapsed }))
    },

    setSidebarCollapsed(collapsed: boolean) {
      update(s => ({ ...s, sidebarCollapsed: collapsed }))
    },

    setImmersive(immersive: boolean) {
      update(s => ({ ...s, immersive }))
    },

    toggleImmersive() {
      update(s => ({ ...s, immersive: !s.immersive }))
    },

    openPanel(panel: Exclude<PanelId, null>) {
      update(s => ({ ...s, activePanel: panel }))
    },

    closePanel() {
      update(s => ({ ...s, activePanel: null }))
    },

    togglePanel(panel: Exclude<PanelId, null>) {
      update(s => ({ ...s, activePanel: s.activePanel === panel ? null : panel }))
    },
  }
}

export const uiStore = createUiStore()
