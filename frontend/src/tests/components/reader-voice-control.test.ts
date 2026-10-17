import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte'

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return { ...actual, getVoices: vi.fn(), setBookVoice: vi.fn() }
})
import * as api from '$lib/api'
import ReaderVoiceControl from '$lib/components/ReaderVoiceControl.svelte'
import { audioStore } from '$lib/stores/audio'

const voices = [
  { id: 'af_heart', name: 'Heart', lang: 'en', gender: 'f', quality: 'a', built_in: true },
  { id: 'af_bella', name: 'Bella', lang: 'en', gender: 'f', quality: 'a', built_in: true },
]

describe('ReaderVoiceControl', () => {
  beforeEach(() => {
    cleanup(); vi.resetAllMocks()
    vi.mocked(api.getVoices).mockResolvedValue(voices)
    vi.mocked(api.setBookVoice).mockResolvedValue({} as never)
  })

  it('confirms, then sets the book voice without touching playback', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const destroy = vi.spyOn(audioStore, 'destroy')
    const onChanged = vi.fn()
    render(ReaderVoiceControl, { props: { bookId: 'b1', currentVoice: 'af_heart', onChanged } })
    const select = await screen.findByRole('combobox', { name: 'Book voice' })
    await fireEvent.change(select, { target: { value: 'af_bella' } })
    await waitFor(() => expect(api.setBookVoice).toHaveBeenCalledWith('b1', 'af_bella'))
    expect(window.confirm).toHaveBeenCalled()
    expect(onChanged).toHaveBeenCalledWith('af_bella')
    expect(destroy).not.toHaveBeenCalled()
  })

  it('does nothing when the confirm is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(ReaderVoiceControl, { props: { bookId: 'b1', currentVoice: 'af_heart' } })
    const select = await screen.findByRole('combobox', { name: 'Book voice' })
    await fireEvent.change(select, { target: { value: 'af_bella' } })
    expect(api.setBookVoice).not.toHaveBeenCalled()
    expect((select as HTMLSelectElement).value).toBe('af_heart')
  })
})
