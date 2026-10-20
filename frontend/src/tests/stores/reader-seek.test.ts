import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { get } from 'svelte/store'

vi.mock('$lib/api', () => ({
  getSentences: vi.fn(),
  getProgress: vi.fn(),
  saveProgress: vi.fn(),
  saveProgressBeacon: vi.fn(),
}))
vi.mock('$lib/stores/user', () => ({ userStore: { updateLastRead: vi.fn().mockResolvedValue(undefined) } }))

import * as api from '$lib/api'
import readerStore, { loadBook, seek, resetReader } from '$lib/stores/reader'

const sentences = Array.from({ length: 20 }, (_, i) => ({ index: i, text: `s${i}`, filtered: false })) as any

describe('reader.seek latency', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    vi.mocked(api.getSentences).mockResolvedValue(sentences)
    vi.mocked(api.getProgress).mockResolvedValue(0)
    resetReader()
    await loadBook('b')
    vi.mocked(api.saveProgress).mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('resolves immediately even when the progress save never completes', async () => {
    vi.mocked(api.saveProgress).mockReturnValue(new Promise(() => {}))
    await seek(7)
    expect(get(readerStore).currentIndex).toBe(7)
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.saveProgress).toHaveBeenCalledTimes(1)
  })

  it('coalesces a burst of seeks into one save of the last position', async () => {
    vi.mocked(api.saveProgress).mockResolvedValue(undefined as any)
    void seek(3); void seek(4); void seek(5)
    expect(api.saveProgress).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(500)
    expect(api.saveProgress).toHaveBeenCalledTimes(1)
    expect(api.saveProgress).toHaveBeenCalledWith('b', 5)
  })
})
