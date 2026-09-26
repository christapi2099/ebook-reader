import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, screen, waitFor, cleanup } from '@testing-library/svelte'
import { get } from 'svelte/store'
import { settingsStore } from '$lib/stores/settings'
import type { Voice } from '$lib/api'

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getVoices: vi.fn(),
    previewVoice: vi.fn(),
    uploadVoice: vi.fn(),
    deleteVoice: vi.fn(),
  }
})

import * as api from '$lib/api'
import VoicePage from '../../routes/voice/+page.svelte'

const VOICES: Voice[] = [
  { id: 'af_heart', name: 'Heart', lang: 'en-US', gender: 'Female', quality: 'A', built_in: true },
  { id: 'am_adam', name: 'Adam', lang: 'en-US', gender: 'Male', quality: 'A', built_in: false },
]

/**
 * Stands in for the browser's `HTMLAudioElement`: `play()` reports itself through
 * the `play` event exactly as a real element does, so the page's status can only
 * change if the element really started.
 */
class FakeAudio {
  static instances: FakeAudio[] = []
  currentTime = 0
  duration = 3
  paused = true
  ended = false
  onplay: (() => void) | null = null
  onpause: (() => void) | null = null
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  readonly src: string
  removedSrc = false
  private listeners = new Map<string, Set<() => void>>()

  constructor(src: string) {
    this.src = src
    FakeAudio.instances.push(this)
  }

  play() {
    this.paused = false
    this.ended = false
    this.onplay?.()
    for (const fn of this.listeners.get('play') ?? []) fn()
    return Promise.resolve()
  }

  pause() {
    this.paused = true
    this.onpause?.()
    for (const fn of this.listeners.get('pause') ?? []) fn()
  }

  removeAttribute(name: string) {
    if (name === 'src') this.removedSrc = true
  }

  addEventListener(type: string, fn: () => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(fn)
  }

  removeEventListener(type: string, fn: () => void) {
    this.listeners.get(type)?.delete(fn)
  }
}

const createObjectURL = vi.fn(() => 'blob:preview-audio')
const revokeObjectURL = vi.fn()

describe('Voices page preview', () => {
  beforeEach(() => {
    cleanup()
    localStorage.clear()
    settingsStore.reset()
    vi.clearAllMocks()
    FakeAudio.instances = []
    vi.stubGlobal('Audio', FakeAudio)
    Object.defineProperty(URL, 'createObjectURL', { writable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: revokeObjectURL })

    vi.mocked(api.getVoices).mockResolvedValue(VOICES)
    vi.mocked(api.previewVoice).mockResolvedValue(new ArrayBuffer(16))
    createObjectURL.mockReturnValue('blob:preview-audio')
  })

  async function renderPage() {
    render(VoicePage)
    await waitFor(() => expect(screen.getByText('Heart')).toBeTruthy())
  }

  it('lists the voices the backend returned', async () => {
    await renderPage()

    expect(screen.getByText('Heart')).toBeTruthy()
    expect(screen.getByText('Adam')).toBeTruthy()
    expect(api.getVoices).toHaveBeenCalled()
  })

  it('plays the real preview bytes when a card is asked for a preview', async () => {
    await renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))

    await waitFor(() => expect(api.previewVoice).toHaveBeenCalledWith('af_heart'))
    const audio = FakeAudio.instances.at(-1)!
    expect(audio.src).toBe('blob:preview-audio')
    expect(audio.paused).toBe(false)
    expect(screen.getByRole('region', { name: 'Voice preview' })).toBeTruthy()
  })

  it('names the voice and the sample sentence in the dock', async () => {
    await renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))

    const dock = await screen.findByRole('region', { name: 'Voice preview' })
    expect(dock.textContent).toContain('Heart')
    expect(dock.textContent).toContain('The quick brown fox jumps over the lazy dog.')
  })

  it('turns the card control into a pause control while it plays', async () => {
    await renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))

    const pauseControl = await screen.findByRole('button', { name: 'Pause preview of Heart' })
    await fireEvent.click(pauseControl)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview Heart' })).toBeTruthy())
    expect(FakeAudio.instances.at(-1)!.paused).toBe(true)
  })

  it('applies the voice with one action and keeps it in the settings store', async () => {
    await renderPage()
    // af_heart is the stored default, so preview the other one.
    await fireEvent.click(screen.getByRole('button', { name: 'Preview Adam' }))

    await fireEvent.click(await screen.findByRole('button', { name: 'Use this voice' }))

    expect(get(settingsStore).voice).toBe('am_adam')
    expect(await screen.findByRole('button', { name: 'Using this voice' })).toBeTruthy()
  })

  it('shows the stored default as already applied', async () => {
    await renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))

    const applied = await screen.findByRole('button', { name: 'Using this voice' })
    expect(applied.getAttribute('aria-pressed')).toBe('true')
  })

  it('stops the preview and releases the object URL', async () => {
    await renderPage()
    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))
    await screen.findByRole('region', { name: 'Voice preview' })

    await fireEvent.click(screen.getByRole('button', { name: 'Stop preview' }))

    await waitFor(() => expect(screen.queryByRole('region', { name: 'Voice preview' })).toBeNull())
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview-audio')
    expect(FakeAudio.instances.at(-1)!.removedSrc).toBe(true)
  })

  it('reports a failed preview without pretending audio is playing', async () => {
    vi.mocked(api.previewVoice).mockRejectedValueOnce(new Error('HTTP 500: Internal Server Error'))
    await renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('Could not play this preview · HTTP 500: Internal Server Error')
    expect(FakeAudio.instances).toHaveLength(0)
  })

  it('releases the previous preview when another voice is previewed', async () => {
    await renderPage()
    await fireEvent.click(screen.getByRole('button', { name: 'Preview Heart' }))
    await screen.findByRole('region', { name: 'Voice preview' })

    await fireEvent.click(screen.getByRole('button', { name: 'Preview Adam' }))

    await waitFor(() => expect(api.previewVoice).toHaveBeenCalledWith('am_adam'))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview-audio')
    const dock = screen.getByRole('region', { name: 'Voice preview' })
    expect(dock.textContent).toContain('Adam')
  })
})
