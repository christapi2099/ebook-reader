import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/svelte'

const goto = vi.fn()
vi.mock('$app/navigation', () => ({ goto: (...a: unknown[]) => goto(...a) }))
vi.mock('$lib/api', async (orig) => ({
  ...(await orig<typeof import('$lib/api')>()),
  createTextBook: vi.fn(async () => ({ book_id: 'tb-1', sentence_count: 2, already_existed: false })),
}))

import Page from '../../routes/+page.svelte'
import { audioStore } from '$lib/stores/audio'
import { createTextBook } from '$lib/api'

describe('home paste-text flow', () => {
  beforeEach(() => {
    cleanup()
    goto.mockClear()
  })

  it('hands the new book to the reader render gate without initialising playback', async () => {
    const init = vi.spyOn(audioStore, 'init')
    render(Page)
    await fireEvent.input(screen.getByPlaceholderText(/Paste or type/), { target: { value: 'Hello there. Bye.' } })
    await fireEvent.click(screen.getByRole('button', { name: /Read Aloud/ }))

    await waitFor(() => expect(goto).toHaveBeenCalledWith('/reader/tb-1'))
    expect(createTextBook).toHaveBeenCalledWith('Hello there. Bye.')
    expect(init).not.toHaveBeenCalled()
  })
})
