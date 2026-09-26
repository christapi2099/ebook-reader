import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, screen, cleanup } from '@testing-library/svelte'
import MediaBar from '$lib/components/MediaBar.svelte'
import { speedDowngradeStore } from '$lib/stores/audio'

function renderBar(overrides: Partial<{
  isPlaying: boolean
  speed: number
  disabled: boolean
  buffering: boolean
}> = {}) {
  const props = {
    isPlaying: true,
    speed: 1.5,
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onRewind: vi.fn(),
    onForward: vi.fn(),
    onSpeedChange: vi.fn(),
    ...overrides,
  }
  render(MediaBar, { props })
  return props
}

function speedButton(label: string): HTMLButtonElement {
  return screen.getByRole('button', { name: label }) as HTMLButtonElement
}

describe('MediaBar speed honesty', () => {
  beforeEach(() => {
    cleanup()
    speedDowngradeStore.set(null)
  })

  afterEach(() => {
    speedDowngradeStore.set(null)
  })

  it('offers the full speed range when the engine has refused nothing', () => {
    renderBar({ speed: 1.5 })

    expect(speedButton('1.5x').disabled).toBe(false)
    expect(speedButton('2x').disabled).toBe(false)
    expect(speedButton('1.5x').getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByText(/Speed limited to/)).toBeNull()
  })

  it('reports a rate change through onSpeedChange', async () => {
    const props = renderBar({ speed: 1.0 })

    await fireEvent.click(speedButton('2x'))

    expect(props.onSpeedChange).toHaveBeenCalledWith(2)
  })

  it('shows a dismissible notice naming the rate it is really playing', () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })

    renderBar({ speed: 1.5 })

    expect(screen.getByText('Speed limited to 1x.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Dismiss speed notice' })).toBeTruthy()
  })

  it('disables every speed button while a rate is refused', () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })

    renderBar({ speed: 1.5 })

    for (const label of ['0.5x', '0.75x', '1x', '1.25x', '1.5x', '2x']) {
      expect(speedButton(label).disabled).toBe(true)
    }
    // Still discoverable: the control is visible, not removed.
    expect(screen.getByRole('group', { name: 'Playback speed' })).toBeTruthy()
  })

  it('highlights the effective rate, not the rate the UI asked for', () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })

    renderBar({ speed: 1.5 })

    expect(speedButton('1x').getAttribute('aria-pressed')).toBe('true')
    expect(speedButton('1.5x').getAttribute('aria-pressed')).toBe('false')
  })

  it('keeps an explanation after the notice is dismissed', async () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })
    renderBar({ speed: 1.5 })

    await fireEvent.click(screen.getByRole('button', { name: 'Dismiss speed notice' }))

    expect(screen.queryByText('Speed limited to 1x.')).toBeNull()
    expect(screen.getByText(/only renders 1x/)).toBeTruthy()
    expect(speedButton('2x').disabled).toBe(true)
  })

  it('brings the notice back for a different refusal', async () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })
    renderBar({ speed: 1.5 })
    await fireEvent.click(screen.getByRole('button', { name: 'Dismiss speed notice' }))
    expect(screen.queryByText('Speed limited to 1x.')).toBeNull()

    speedDowngradeStore.set({ requested: 2.0, effective: 1.0 })

    expect(await screen.findByText('Speed limited to 1x.')).toBeTruthy()
  })

  it('does not lock transport controls, only the speed range', async () => {
    speedDowngradeStore.set({ requested: 1.5, effective: 1.0 })
    const props = renderBar({ isPlaying: true, speed: 1.5 })

    await fireEvent.click(screen.getByRole('button', { name: 'Pause' }))

    expect(props.onPause).toHaveBeenCalled()
    expect(speedButton('1.5x').disabled).toBe(true)
  })

  it('says the engine cannot render the requested rate, in the notice text', () => {
    speedDowngradeStore.set({ requested: 2.0, effective: 1.0 })

    renderBar({ speed: 2.0 })

    expect(screen.getByText(/cannot render at 2x/)).toBeTruthy()
  })
})
