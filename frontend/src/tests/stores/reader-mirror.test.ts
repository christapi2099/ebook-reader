import { describe, it, expect, vi, beforeEach } from 'vitest'
import { get } from 'svelte/store'

vi.mock('$lib/api', () => ({
  getSentences: vi.fn(),
  getProgress: vi.fn(),
  saveProgress: vi.fn().mockResolvedValue(undefined),
  saveProgressBeacon: vi.fn(),
}))
vi.mock('$lib/stores/user', () => ({ userStore: { updateLastRead: vi.fn().mockResolvedValue(undefined) } }))

import * as api from '$lib/api'
import readerStore, { loadBook, mirrorPlaybackIndex, resetReader } from '$lib/stores/reader'

const sentences = Array.from({ length: 10 }, (_, i) => ({ index: i, text: `s${i}`, filtered: false })) as any

describe('mirrorPlaybackIndex', () => {
  beforeEach(async () => {
    vi.mocked(api.getSentences).mockResolvedValue(sentences)
    vi.mocked(api.getProgress).mockResolvedValue(0)
    resetReader()
    await loadBook('b')
  })

  it('an unchanged index does not notify subscribers (it runs on every audio frame)', () => {
    mirrorPlaybackIndex(3, true)
    let calls = 0
    const unsub = readerStore.subscribe(() => { calls++ })
    calls = 0
    for (let i = 0; i < 50; i++) mirrorPlaybackIndex(3, true)
    expect(calls).toBe(0)
    mirrorPlaybackIndex(4, true)
    expect(calls).toBe(1)
    expect(get(readerStore).currentIndex).toBe(4)
    unsub()
  })

  it('keeps the sentences array identity when the index moves', () => {
    const before = get(readerStore).sentences
    mirrorPlaybackIndex(6, true)
    expect(get(readerStore).sentences).toBe(before)
  })
})
