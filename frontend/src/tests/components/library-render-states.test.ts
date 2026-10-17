import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte'
import LibraryCard from '$lib/components/LibraryCard.svelte'
import LibraryPage from '../../routes/library/+page.svelte'
import type { Book } from '$lib/api'

vi.mock('$app/navigation', () => ({ goto: vi.fn() }))
vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getLibrary: vi.fn(), getFolders: vi.fn(), reprocessBook: vi.fn(),
    getUserSettings: vi.fn(), getProgress: vi.fn(), getSentences: vi.fn(),
  }
})
import * as api from '$lib/api'

const base: Book = { id: 'b1', title: 'Deep Work', author: null, file_type: 'pdf', page_count: 9, folder_id: null }

function card(extra: Partial<Book>, props: Record<string, unknown> = {}) {
  const onClick = vi.fn(); const onRetry = vi.fn()
  render(LibraryCard, { props: { book: { ...base, ...extra }, onClick, onRetry, ...props } })
  return { onClick, onRetry }
}

describe('LibraryCard render states', () => {
  beforeEach(cleanup)

  it('locked: shows progress, ETA and queue position, and does not open', async () => {
    const { onClick } = card({ job_status: 'pending', progress: 0, eta_seconds: 300, queue_position: 2 })
    const text = document.querySelector('[data-locked-status]')!.textContent!
    expect(text).toContain('#2 in queue')
    expect(text).toContain('about 5 min')
    await fireEvent.click(screen.getByRole('button', { name: /Deep Work/ }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('locked while processing shows the percentage', () => {
    card({ job_status: 'processing', progress: 0.42, eta_seconds: 30 })
    expect(document.querySelector('[data-locked-status]')!.textContent).toContain('Processing 42%')
    expect(screen.getByRole('progressbar', { name: /Processing/ }).getAttribute('aria-valuenow')).toBe('42')
  })

  it('re-rendering: badge shown but still openable', async () => {
    const { onClick } = card({ job_status: 'processing', ready_generation: 2, progress: 0.1 })
    expect(screen.getByText('Re-rendering…')).toBeTruthy()
    expect(document.querySelector('[data-locked-status]')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: /Deep Work/ }))
    expect(onClick).toHaveBeenCalledWith('b1')
  })

  it('error: Retry calls onRetry without opening the book', async () => {
    const { onClick, onRetry } = card({ job_status: 'error' })
    await fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(onRetry).toHaveBeenCalledWith('b1')
    expect(onClick).not.toHaveBeenCalled()
  })

  it('ready book shows none of the states', () => {
    card({ job_status: 'ready', ready_generation: 1 })
    expect(document.querySelector('[data-render-state="ready"]')).toBeTruthy()
    expect(screen.queryByText('Re-rendering…')).toBeNull()
  })
})

describe('Library polling', () => {
  beforeEach(() => {
    cleanup(); vi.useFakeTimers({ shouldAdvanceTime: false })
    vi.mocked(api.getFolders).mockResolvedValue([])
    vi.mocked(api.getUserSettings).mockResolvedValue({} as never)
  })
  afterEach(() => { vi.useRealTimers() })

  it('polls every 3 s while a job is active and stops once idle', async () => {
    const active = { ...base, job_status: 'processing' as const, progress: 0.2 }
    const idle = { ...base, job_status: 'ready' as const, ready_generation: 1 }
    vi.mocked(api.getLibrary).mockResolvedValue([active])
    render(LibraryPage)
    await vi.advanceTimersByTimeAsync(10)
    const initial = vi.mocked(api.getLibrary).mock.calls.length

    await vi.advanceTimersByTimeAsync(3000)
    expect(vi.mocked(api.getLibrary).mock.calls.length).toBe(initial + 1)

    vi.mocked(api.getLibrary).mockResolvedValue([idle])
    await vi.advanceTimersByTimeAsync(3000)
    const afterIdle = vi.mocked(api.getLibrary).mock.calls.length
    expect(afterIdle).toBe(initial + 2)

    await vi.advanceTimersByTimeAsync(12000)
    expect(vi.mocked(api.getLibrary).mock.calls.length).toBe(afterIdle)
  })

  it('does not poll when nothing is active', async () => {
    vi.mocked(api.getLibrary).mockResolvedValue([{ ...base, job_status: 'ready', ready_generation: 1 }])
    render(LibraryPage)
    await vi.advanceTimersByTimeAsync(10)
    const n = vi.mocked(api.getLibrary).mock.calls.length
    await vi.advanceTimersByTimeAsync(9000)
    expect(vi.mocked(api.getLibrary).mock.calls.length).toBe(n)
  })

  it('Retry on an error card re-queues via reprocessBook', async () => {
    vi.mocked(api.getLibrary).mockResolvedValue([{ ...base, job_status: 'error' }])
    vi.mocked(api.reprocessBook).mockResolvedValue({ ...base } as never)
    render(LibraryPage)
    await vi.advanceTimersByTimeAsync(10)
    await fireEvent.click(screen.getByRole('button', { name: /Retry Deep Work/ }))
    expect(api.reprocessBook).toHaveBeenCalledWith('b1')
  })
})
