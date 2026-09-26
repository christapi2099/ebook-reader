import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/svelte'
import VoicePreviewDock from '$lib/components/VoicePreviewDock.svelte'
import { VOICE_PREVIEW_SAMPLE_TEXT, type Voice } from '$lib/api'
import { WAVEFORM_BUCKETS } from '$lib/utils/waveform'

const VOICE: Voice = {
  id: 'af_heart',
  name: 'Heart',
  lang: 'en-US',
  gender: 'Female',
  quality: 'A',
  built_in: true,
}

/**
 * Stands in for the page's real `HTMLAudioElement`. The dock only reads these
 * members, so a stub is enough to prove the strip follows the element's clock.
 */
function stubAudio(currentTime = 0, duration = 4) {
  const listeners = new Map<string, Set<() => void>>()
  return {
    currentTime,
    duration,
    addEventListener(type: string, fn: () => void) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)!.add(fn)
    },
    removeEventListener(type: string, fn: () => void) {
      listeners.get(type)?.delete(fn)
    },
    emit(type: string) {
      for (const fn of listeners.get(type) ?? []) fn()
    },
    listenerCount(type: string) {
      return listeners.get(type)?.size ?? 0
    },
  } as unknown as HTMLAudioElement & { emit: (t: string) => void; listenerCount: (t: string) => number }
}

function renderDock(overrides: Record<string, unknown> = {}) {
  const props = {
    voice: VOICE,
    audio: null,
    bytes: null,
    status: 'paused' as const,
    selected: false,
    error: null,
    onTogglePlay: vi.fn(),
    onStop: vi.fn(),
    onUseVoice: vi.fn(),
    ...overrides,
  }
  render(VoicePreviewDock, { props })
  return props
}

function progress(): HTMLElement {
  return screen.getByRole('progressbar', { name: 'Preview progress' })
}

describe('VoicePreviewDock', () => {
  beforeEach(() => {
    cleanup()
  })

  afterEach(() => {
    cleanup()
  })

  it('names the voice and the sentence the backend really synthesises', () => {
    renderDock()

    expect(screen.getByText('Heart')).toBeTruthy()
    expect(screen.getByText(VOICE_PREVIEW_SAMPLE_TEXT)).toBeTruthy()
  })

  it('offers play, stop and one primary action', () => {
    renderDock()

    expect(screen.getByRole('button', { name: 'Play preview' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Stop preview' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Use this voice' })).toBeTruthy()
  })

  it('reports the play position from the audio element, not a timer', async () => {
    const audio = stubAudio(0, 4)
    renderDock({ audio, status: 'playing' })

    expect(progress().getAttribute('aria-valuenow')).toBe('0')
    // The element listener is installed by an effect, so wait for it.
    await waitFor(() => expect(audio.listenerCount('timeupdate')).toBeGreaterThan(0))

    audio.currentTime = 1
    audio.emit('timeupdate')
    await waitFor(() => expect(progress().getAttribute('aria-valuenow')).toBe('25'))

    audio.currentTime = 3
    audio.emit('timeupdate')
    await waitFor(() => expect(progress().getAttribute('aria-valuenow')).toBe('75'))
  })

  it('shows no progress while the duration is still unknown', () => {
    const audio = stubAudio(0, NaN)
    renderDock({ audio, status: 'playing' })

    expect(progress().getAttribute('aria-valuenow')).toBe('0')
  })

  it('renders one bar per waveform bucket, with no invented amplitudes', () => {
    renderDock()

    const strip = progress().querySelector('[aria-hidden="true"]') as HTMLElement
    const bars = Array.from(strip.children) as HTMLElement[]
    expect(bars).toHaveLength(WAVEFORM_BUCKETS)
    // No Web Audio in this environment: a flat trough, not made-up peaks.
    const heights = new Set(bars.map(bar => bar.style.height))
    expect(heights.size).toBe(1)
  })

  it('toggles playback, stops, and applies the voice through callbacks', async () => {
    const props = renderDock()

    await fireEvent.click(screen.getByRole('button', { name: 'Play preview' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Stop preview' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Use this voice' }))

    expect(props.onTogglePlay).toHaveBeenCalledTimes(1)
    expect(props.onStop).toHaveBeenCalledTimes(1)
    expect(props.onUseVoice).toHaveBeenCalledTimes(1)
  })

  it('shows the pause affordance while the element is playing', () => {
    renderDock({ status: 'playing' })

    expect(screen.getByRole('button', { name: 'Pause preview' })).toBeTruthy()
  })

  it('marks the active voice as already applied', () => {
    renderDock({ selected: true })

    const button = screen.getByRole('button', { name: 'Using this voice' })
    expect(button.getAttribute('aria-pressed')).toBe('true')
  })

  it('disables play while the bytes are still loading', () => {
    renderDock({ status: 'loading' })

    const play = screen.getByRole('button', { name: 'Play preview' }) as HTMLButtonElement
    expect(play.disabled).toBe(true)
    expect(play.getAttribute('data-loading')).toBe('true')
  })

  it('surfaces a failed preview as an alert alongside a retry', () => {
    renderDock({ status: 'paused', error: 'Could not play this preview · HTTP 500' })

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toBe('Could not play this preview · HTTP 500')
    expect(screen.getByRole('button', { name: 'Play preview' })).toBeTruthy()
  })

  it('follows a new element when the preview target changes', () => {
    const first = stubAudio(1, 4)
    const view = renderDock({ audio: first, status: 'playing' })
    expect(first.listenerCount('timeupdate')).toBeGreaterThan(0)

    cleanup()
    const second = stubAudio(2, 4)
    render(VoicePreviewDock, {
      props: {
        voice: VOICE,
        audio: second,
        bytes: null,
        status: 'playing',
        onTogglePlay: vi.fn(),
        onStop: vi.fn(),
        onUseVoice: vi.fn(),
      },
    })

    expect(second.listenerCount('timeupdate')).toBeGreaterThan(0)
    expect(progress().getAttribute('aria-valuenow')).toBe('50')
    expect(view.onTogglePlay).not.toHaveBeenCalled()
  })
})
