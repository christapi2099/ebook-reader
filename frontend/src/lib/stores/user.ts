import { writable } from 'svelte/store'
import type { UserSettings } from '$lib/api'
import { getUserSettings, updateUserSettings } from '$lib/api'

interface UserStoreState {
  settings: UserSettings
  loading: boolean
  error: string | null
}

const defaultSettings: UserSettings = {
  last_book_id: null,
  last_sentence_index: 0,
}

function createUserStore() {
  const initialState: UserStoreState = {
    settings: defaultSettings,
    loading: false,
    error: null,
  }

  const { subscribe, set, update } = writable<UserStoreState>(initialState)

  return {
    subscribe,

    async load(): Promise<void> {
      update((s) => ({ ...s, loading: true, error: null }))
      try {
        const settings = await getUserSettings()
        update((s) => ({ ...s, settings, loading: false }))
      } catch (e: any) {
        update((s) => ({
          ...s,
          loading: false,
          error: 'Failed to load user settings',
        }))
      }
    },

    async updateLastRead(bookId: string, sentenceIndex: number): Promise<void> {
      let previousSettings: UserSettings
      update((s) => {
        previousSettings = s.settings
        return { ...s, loading: true, error: null }
      })
      try {
        await updateUserSettings({
          last_book_id: bookId,
          last_sentence_index: sentenceIndex,
        })
        // Only update state on success
        update((s) => ({
          ...s,
          settings: {
            last_book_id: bookId,
            last_sentence_index: sentenceIndex,
          },
          loading: false,
        }))
      } catch (e: any) {
        // Restore previous settings on error
        update((s) => ({
          ...s,
          settings: previousSettings!,
          loading: false,
          error: 'Failed to update user settings',
        }))
        // Re-throw to allow caller to handle if needed
        throw e
      }
    },

    async clearLastRead(): Promise<void> {
      update((s) => ({ ...s, loading: true, error: null }))
      try {
        await updateUserSettings({ last_book_id: null })
        update((s) => ({
          ...s,
          settings: { ...s.settings, last_book_id: null },
          loading: false,
        }))
      } catch (e: any) {
        update((s) => ({
          ...s,
          loading: false,
          error: 'Failed to clear last read',
        }))
      }
    },
  }
}

export const userStore = createUserStore()
