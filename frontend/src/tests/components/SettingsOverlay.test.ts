import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, screen, waitFor, cleanup, within } from '@testing-library/svelte'
import { get } from 'svelte/store'
import { settingsStore } from '$lib/stores/settings'
import { ApiError } from '$lib/utils/errors'
import type { EngineState, SystemCapabilities } from '$lib/api'

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return {
    ...actual,
    getEngineState: vi.fn(),
    getCapabilities: vi.fn(),
    setEngine: vi.fn(),
  }
})

import * as api from '$lib/api'
import SettingsOverlay from '$lib/components/SettingsOverlay.svelte'

const ENGINE_STATE: EngineState = {
  selected: 'cpu',
  active: 'cpu',
  switching: false,
  phase: 'idle',
  options: [
    { id: 'cpu', label: 'This device · CPU', available: true, reason: null },
    { id: 'gpu', label: 'This device · GPU', available: false, reason: 'No CUDA GPU detected' },
    { id: 'modal', label: 'Cloud · Modal GPU', available: true, reason: null },
  ],
}

const CAPABILITIES: SystemCapabilities = {
  active_backend: 'local',
  requested_backend: 'local',
  synthesis_available: true,
  sample_rate: 24000,
  local: {
    torch_version: '2.9.0',
    torch_cuda_version: '12.8',
    cuda_available: false,
    cuda_device_count: 0,
    gpu_name: null,
    device_in_use: 'cpu',
    model_repo: 'hexgrad/Kokoro-82M',
    error: null,
  },
  remote: {
    configured: true,
    resolved_transport: 'sdk',
    credentials_present: true,
    reachable: false,
    error: 'Modal app did not answer',
    would_use: 'sdk',
  },
  errors: { startup: null, remote: null },
}

const SWITCH_NAMES = [
  'Sentence Highlight',
  'Auto-Scroll',
  'Keyboard Hotkeys',
  'Bionic Reading',
]

function renderSettings() {
  const onClose = vi.fn()
  render(SettingsOverlay, { props: { onClose } })
  return onClose
}

async function waitForEngine() {
  await waitFor(() => expect(screen.getByText('This device · CPU')).toBeTruthy())
}

describe('SettingsOverlay', () => {
  beforeEach(() => {
    cleanup()
    localStorage.clear()
    settingsStore.reset()
    vi.clearAllMocks()
    vi.mocked(api.getEngineState).mockResolvedValue(ENGINE_STATE)
    vi.mocked(api.getCapabilities).mockResolvedValue(CAPABILITIES)
  })

  describe('switch naming', () => {
    it('gives every switch the accessible name of its visible label', async () => {
      renderSettings()

      for (const name of SWITCH_NAMES) {
        const toggle = screen.getByRole('switch', { name })
        expect(toggle).toBeTruthy()
        // The visible label is inside the row the switch belongs to.
        expect(toggle.getAttribute('aria-checked')).toBe(
          name === 'Bionic Reading' ? 'false' : 'true',
        )
      }
      expect(screen.getAllByRole('switch')).toHaveLength(SWITCH_NAMES.length)
    })

    it('names the skip-common-words switch once bionic is on', async () => {
      renderSettings()

      await fireEvent.click(screen.getByRole('switch', { name: 'Bionic Reading' }))

      expect(screen.getByRole('switch', { name: 'Skip Common Words' })).toBeTruthy()
      expect(screen.getAllByRole('switch')).toHaveLength(SWITCH_NAMES.length + 1)
    })

    it('does not rely on positional switch order', async () => {
      renderSettings()

      // Toggling by name hits the right setting even with the bionic block open.
      await fireEvent.click(screen.getByRole('switch', { name: 'Auto-Scroll' }))

      expect(get(settingsStore).autoscroll).toBe(false)
      expect(get(settingsStore).hotkeysEnabled).toBe(true)
      expect(get(settingsStore).highlightEnabled).toBe(true)
    })
  })

  describe('bionic config', () => {
    it('wires the min word length slider to the store', async () => {
      renderSettings()
      await fireEvent.click(screen.getByRole('switch', { name: 'Bionic Reading' }))

      const slider = document.getElementById('bionic-min-length') as HTMLInputElement
      expect(slider).toBeTruthy()
      await fireEvent.input(slider, { target: { value: '6' } })

      expect(get(settingsStore).bionicMinWordLength).toBe(6)
      expect(screen.getByText('Minimum word length: 6')).toBeTruthy()
      const stored = JSON.parse(localStorage.getItem('kokoro-settings') || '{}')
      expect(stored.bionicMinWordLength).toBe(6)
    })

    it('wires the skip-common-words switch to the store', async () => {
      renderSettings()
      await fireEvent.click(screen.getByRole('switch', { name: 'Bionic Reading' }))

      await fireEvent.click(screen.getByRole('switch', { name: 'Skip Common Words' }))

      expect(get(settingsStore).bionicSkipCommonWords).toBe(false)
      const stored = JSON.parse(localStorage.getItem('kokoro-settings') || '{}')
      expect(stored.bionicSkipCommonWords).toBe(false)
    })
  })

  describe('processing engine', () => {
    it('lists every engine with the probe verdict', async () => {
      renderSettings()
      await waitForEngine()

      expect(screen.getByText('This device · GPU')).toBeTruthy()
      expect(screen.getByText('No CUDA GPU detected')).toBeTruthy()
      expect(screen.getByText('Cloud · Modal GPU')).toBeTruthy()
      expect(screen.getByText('Live now')).toBeTruthy()
      // The two usable engines that are not live: CPU is live, modal is available.
      expect(screen.getAllByText('Available')).toHaveLength(2)
    })

    it('reports the capabilities probe instead of guessing', async () => {
      renderSettings()
      await waitForEngine()

      const facts = screen.getByText('Synthesis').closest('dl') as HTMLElement
      expect(within(facts).getByText('Available')).toBeTruthy()
      expect(within(facts).getByText('24000 Hz')).toBeTruthy()
      expect(within(facts).getByText('cpu')).toBeTruthy()
      expect(within(facts).getByText('Configured · unreachable')).toBeTruthy()
      // No invented health: an error the probe did not report is not rendered.
      expect(screen.queryByText(/Torch error/)).toBeNull()
      expect(screen.queryByText(/Cloud error/)).toBeNull()
    })

    it('shows the GPU name when torch reports one', async () => {
      vi.mocked(api.getCapabilities).mockResolvedValue({
        ...CAPABILITIES,
        local: { ...CAPABILITIES.local, cuda_available: true, gpu_name: 'NVIDIA T600 Laptop GPU' },
      })
      renderSettings()
      await waitForEngine()

      expect(screen.getByText('NVIDIA T600 Laptop GPU')).toBeTruthy()
    })

    it('switches the live engine through the real endpoint', async () => {
      vi.mocked(api.setEngine).mockResolvedValue({
        ...ENGINE_STATE,
        selected: 'modal',
        active: 'modal',
      })
      renderSettings()
      await waitForEngine()

      await fireEvent.click(screen.getByRole('radio', { name: /Cloud · Modal GPU/ }))

      await waitFor(() => expect(api.setEngine).toHaveBeenCalledWith('modal'))
      const live = screen.getByRole('radio', { name: /Cloud · Modal GPU/ }) as HTMLInputElement
      expect(live.checked).toBe(true)
    })

    it('never offers an engine the probe says this machine cannot run', async () => {
      renderSettings()
      await waitForEngine()

      const gpu = screen.getByRole('radio', { name: /This device · GPU/ }) as HTMLInputElement
      expect(gpu.disabled).toBe(true)

      await fireEvent.click(gpu)
      expect(api.setEngine).not.toHaveBeenCalled()
    })

    it('surfaces the server’s reason when a switch is refused', async () => {
      vi.mocked(api.setEngine).mockRejectedValue(
        new ApiError(409, 'Conflict', 'No CUDA GPU detected'),
      )
      renderSettings()
      await waitForEngine()

      await fireEvent.click(screen.getByRole('radio', { name: /Cloud · Modal GPU/ }))

      const alert = await screen.findByRole('alert')
      expect(alert.textContent).toBe('No CUDA GPU detected')
      // The list stays up so the user can pick something else.
      expect(screen.getByText('This device · CPU')).toBeTruthy()
    })

    it('says so when the engine state cannot be read, and retries', async () => {
      vi.mocked(api.getEngineState).mockRejectedValueOnce(new TypeError('Failed to fetch'))
      renderSettings()

      expect(await screen.findByText('Could not connect to the backend')).toBeTruthy()

      vi.mocked(api.getEngineState).mockResolvedValue(ENGINE_STATE)
      await fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

      await waitForEngine()
    })
  })

  it('closes through the onClose callback', async () => {
    const onClose = renderSettings()

    await fireEvent.click(screen.getByRole('button', { name: 'Close settings' }))

    expect(onClose).toHaveBeenCalled()
  })
})
