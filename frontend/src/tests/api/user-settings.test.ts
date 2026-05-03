import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  getUserSettings,
  updateUserSettings,
  type UserSettings,
} from '$lib/api'

describe('User Settings API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = vi.fn()
  })

  describe('getUserSettings', () => {
    it('fetches user settings successfully', async () => {
      const mockSettings: UserSettings = {
        last_book_id: 'test-book-id',
        last_sentence_index: 42,
      }
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockSettings,
      })

      const result = await getUserSettings()
      expect(result).toEqual(mockSettings)
      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/user/settings',
        {
          headers: { 'Content-Type': 'application/json' },
        }
      )
    })

    it('throws error on non-OK response', async () => {
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: 'Not Found',
      })

      await expect(getUserSettings()).rejects.toThrow('HTTP 404: Not Found')
    })

    it('returns default settings if none exist', async () => {
      const mockSettings: UserSettings = {
        last_book_id: null,
        last_sentence_index: 0,
      }
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockSettings,
      })

      const result = await getUserSettings()
      expect(result).toEqual(mockSettings)
    })
  })

  describe('updateUserSettings', () => {
    it('updates user settings successfully', async () => {
      const updateData = {
        last_book_id: 'new-book-id',
        last_sentence_index: 10,
      }
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      })

      const result = await updateUserSettings(updateData)
      expect(result).toEqual({ ok: true })
      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/user/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updateData),
        }
      )
    })

    it('throws error on non-OK response', async () => {
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
      })

      await expect(
        updateUserSettings({ last_sentence_index: 5 })
      ).rejects.toThrow('HTTP 500: Internal Server Error')
    })

    it('handles partial updates (only sentence index)', async () => {
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      })

      await updateUserSettings({ last_sentence_index: 20 })

      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/user/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ last_sentence_index: 20 }),
        }
      )
    })

    it('handles partial updates (only book id)', async () => {
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      })

      await updateUserSettings({ last_book_id: 'some-book' })

      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/user/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ last_book_id: 'some-book' }),
        }
      )
    })

    it('handles null book id updates', async () => {
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      })

      await updateUserSettings({ last_book_id: null })

      expect(global.fetch).toHaveBeenCalledWith(
        'http://localhost:8000/user/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ last_book_id: null }),
        }
      )
    })
  })
})
