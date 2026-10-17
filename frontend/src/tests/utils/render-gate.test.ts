import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createRenderGate } from '$lib/utils/render-gate'
import type { Book } from '$lib/api'

const book = (extra: Partial<Book>): Book =>
  ({ id: 'b', title: 't', author: null, file_type: 'pdf', page_count: 1, folder_id: null, ...extra })

describe('createRenderGate', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('with no generation, polls every 3 s and fires onReady once ready', async () => {
    const getBook = vi.fn()
      .mockResolvedValueOnce(book({ job_status: 'processing' }))
      .mockResolvedValueOnce(book({ job_status: 'processing' }))
      .mockResolvedValue(book({ ready_generation: 1, job_status: 'ready' }))
    const onReady = vi.fn()
    const gate = createRenderGate({ bookId: 'b', getBook, loadedGeneration: null, onReady, onNewer: vi.fn() })
    gate.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onReady).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3000)
    expect(onReady).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3000)
    expect(onReady).toHaveBeenCalledWith(1, expect.anything())
    gate.stop()
  })

  it('with a loaded generation, polls every 10 s and reports only a newer one', async () => {
    const getBook = vi.fn()
      .mockResolvedValueOnce(book({ ready_generation: 2 }))
      .mockResolvedValue(book({ ready_generation: 3, voice: 'af_bella' }))
    const onNewer = vi.fn(); const onReady = vi.fn()
    const gate = createRenderGate({ bookId: 'b', getBook, loadedGeneration: 2, onReady, onNewer })
    gate.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onNewer).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(9000)
    expect(getBook).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(onNewer).toHaveBeenCalledWith(3, expect.objectContaining({ voice: 'af_bella' }))
    expect(onReady).not.toHaveBeenCalled()
    gate.stop()
  })

  it('checks on window focus and stops cleanly', async () => {
    const getBook = vi.fn().mockResolvedValue(book({ ready_generation: 1 }))
    const gate = createRenderGate({ bookId: 'b', getBook, loadedGeneration: 1, onReady: vi.fn(), onNewer: vi.fn() })
    gate.start()
    await vi.advanceTimersByTimeAsync(0)
    getBook.mockClear()
    window.dispatchEvent(new Event('focus'))
    expect(getBook).toHaveBeenCalledTimes(1)
    gate.stop()
    getBook.mockClear()
    window.dispatchEvent(new Event('focus'))
    await vi.advanceTimersByTimeAsync(60_000)
    expect(getBook).not.toHaveBeenCalled()
  })

  it('survives a failed poll', async () => {
    const getBook = vi.fn().mockRejectedValueOnce(new TypeError('down'))
      .mockResolvedValue(book({ ready_generation: 1 }))
    const onReady = vi.fn()
    const gate = createRenderGate({ bookId: 'b', getBook, loadedGeneration: null, onReady, onNewer: vi.fn() })
    gate.start()
    await vi.advanceTimersByTimeAsync(3000)
    expect(onReady).toHaveBeenCalledWith(1, expect.anything())
    gate.stop()
  })
})
