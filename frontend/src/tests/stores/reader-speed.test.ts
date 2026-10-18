import { describe, it, expect, beforeEach, vi } from 'vitest'
import { get } from 'svelte/store'

vi.mock('$lib/api')

const KEY = 'kokoro-playback-speed'

// The store reads localStorage when the module is evaluated, so each test
// re-imports it after seeding storage.
async function loadStore() {
  vi.resetModules()
  return import('$lib/stores/reader')
}

describe('reader playback speed persistence', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('restores a speed saved before the page was reloaded', async () => {
    localStorage.setItem(KEY, '1.5')
    const { default: readerStore } = await loadStore()
    expect(get(readerStore).speed).toBe(1.5)
  })

  it('defaults to 1x when nothing is saved', async () => {
    const { default: readerStore } = await loadStore()
    expect(get(readerStore).speed).toBe(1)
  })

  it('ignores a corrupt saved value', async () => {
    localStorage.setItem(KEY, 'fast')
    const { default: readerStore } = await loadStore()
    expect(get(readerStore).speed).toBe(1)
  })

  it('clamps an out-of-range saved value', async () => {
    localStorage.setItem(KEY, '9')
    const { default: readerStore } = await loadStore()
    expect(get(readerStore).speed).toBe(3)
  })

  it('writes the speed whenever it is set', async () => {
    const { setSpeed } = await loadStore()
    setSpeed(2)
    expect(localStorage.getItem(KEY)).toBe('2')
  })

  it('keeps the saved speed across resetReader', async () => {
    const { default: readerStore, setSpeed, resetReader } = await loadStore()
    setSpeed(0.75)
    resetReader()
    expect(get(readerStore).speed).toBe(0.75)
  })

  it('survives localStorage throwing', async () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    try {
      const { default: readerStore, setSpeed } = await loadStore()
      expect(get(readerStore).speed).toBe(1)
      expect(() => setSpeed(1.25)).not.toThrow()
      expect(get(readerStore).speed).toBe(1.25)
    } finally {
      getSpy.mockRestore()
      setSpy.mockRestore()
    }
  })
})
