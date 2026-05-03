import { describe, it, expect, beforeEach, vi } from 'vitest'
import { get } from 'svelte/store'
import { userStore } from '$lib/stores/user'
import * as api from '$lib/api'

vi.mock('$lib/api')

describe('userStore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('has correct default values', () => {
      const state = get(userStore)
      expect(state.settings).toEqual({
        last_book_id: null,
        last_sentence_index: 0,
      })
      expect(state.loading).toBe(false)
      expect(state.error).toBe(null)
    })
  })

  describe('load', () => {
    it('loads user settings successfully', async () => {
      const mockSettings = {
        last_book_id: 'test-book-id',
        last_sentence_index: 42,
      }
      vi.mocked(api.getUserSettings).mockResolvedValueOnce(mockSettings)

      await userStore.load()

      const state = get(userStore)
      expect(state.settings).toEqual(mockSettings)
      expect(state.loading).toBe(false)
      expect(state.error).toBe(null)
      expect(api.getUserSettings).toHaveBeenCalledTimes(1)
    })

    it('sets loading state while loading', async () => {
      vi.mocked(api.getUserSettings).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({
          last_book_id: null,
          last_sentence_index: 0,
        }), 100))
      )

      const loadPromise = userStore.load()

      // Check loading state
      let state = get(userStore)
      expect(state.loading).toBe(true)

      await loadPromise

      // Check final state
      state = get(userStore)
      expect(state.loading).toBe(false)
    })

    it('handles load errors gracefully', async () => {
      vi.mocked(api.getUserSettings).mockRejectedValueOnce(
        new Error('Network error')
      )

      await userStore.load()

      const state = get(userStore)
      expect(state.loading).toBe(false)
      expect(state.error).toBe('Failed to load user settings')
      expect(state.settings).toEqual({
        last_book_id: null,
        last_sentence_index: 0,
      })
    })

    it('maintains existing settings on load error', async () => {
      // Set some initial settings
      vi.mocked(api.getUserSettings).mockResolvedValueOnce({
        last_book_id: 'old-book',
        last_sentence_index: 10,
      })
      await userStore.load()

      // Now fail the next load
      vi.mocked(api.getUserSettings).mockRejectedValueOnce(
        new Error('Network error')
      )
      await userStore.load()

      const state = get(userStore)
      expect(state.settings.last_book_id).toBe('old-book')
      expect(state.error).toBe('Failed to load user settings')
    })
  })

  describe('updateLastRead', () => {
    it('updates last read successfully', async () => {
      vi.mocked(api.updateUserSettings).mockResolvedValueOnce({ ok: true })

      await userStore.updateLastRead('book-123', 25)

      const state = get(userStore)
      expect(state.settings).toEqual({
        last_book_id: 'book-123',
        last_sentence_index: 25,
      })
      expect(state.loading).toBe(false)
      expect(state.error).toBe(null)
      expect(api.updateUserSettings).toHaveBeenCalledWith({
        last_book_id: 'book-123',
        last_sentence_index: 25,
      })
    })

    it('sets loading state while updating', async () => {
      vi.mocked(api.updateUserSettings).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 100))
      )

      const updatePromise = userStore.updateLastRead('book-123', 25)

      // Check loading state
      let state = get(userStore)
      expect(state.loading).toBe(true)

      await updatePromise

      // Check final state
      state = get(userStore)
      expect(state.loading).toBe(false)
    })

    it('handles update errors gracefully', async () => {
      vi.mocked(api.updateUserSettings).mockRejectedValueOnce(
        new Error('Network error')
      )

      // The error should be thrown
      await expect(userStore.updateLastRead('book-123', 25)).rejects.toThrow('Network error')

      const state = get(userStore)
      expect(state.loading).toBe(false)
      expect(state.error).toBe('Failed to update user settings')
      // Settings might be in inconsistent state after error, but error should be set
      expect(state.error).not.toBe(null)
    })

    it('updates settings in-place on success', async () => {
      // Set initial settings
      vi.mocked(api.getUserSettings).mockResolvedValueOnce({
        last_book_id: 'old-book',
        last_sentence_index: 10,
      })
      await userStore.load()

      // Update to new settings
      vi.mocked(api.updateUserSettings).mockResolvedValueOnce({ ok: true })
      await userStore.updateLastRead('new-book', 20)

      const state = get(userStore)
      expect(state.settings).toEqual({
        last_book_id: 'new-book',
        last_sentence_index: 20,
      })
    })
  })

  describe('clearLastRead', () => {
    it('clears last read successfully', async () => {
      // Set initial settings
      vi.mocked(api.getUserSettings).mockResolvedValueOnce({
        last_book_id: 'book-123',
        last_sentence_index: 25,
      })
      await userStore.load()

      // Clear last read
      vi.mocked(api.updateUserSettings).mockResolvedValueOnce({ ok: true })
      await userStore.clearLastRead()

      const state = get(userStore)
      expect(state.settings.last_book_id).toBe(null)
      expect(state.settings.last_sentence_index).toBe(25)
      expect(state.loading).toBe(false)
      expect(state.error).toBe(null)
      expect(api.updateUserSettings).toHaveBeenCalledWith({
        last_book_id: null,
      })
    })

    it('sets loading state while clearing', async () => {
      vi.mocked(api.updateUserSettings).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 100))
      )

      const clearPromise = userStore.clearLastRead()

      // Check loading state
      let state = get(userStore)
      expect(state.loading).toBe(true)

      await clearPromise

      // Check final state
      state = get(userStore)
      expect(state.loading).toBe(false)
    })

    it('handles clear errors gracefully', async () => {
      vi.mocked(api.updateUserSettings).mockRejectedValueOnce(
        new Error('Network error')
      )

      await userStore.clearLastRead()

      const state = get(userStore)
      expect(state.loading).toBe(false)
      expect(state.error).toBe('Failed to clear last read')
    })
  })
})
