import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, screen, waitFor, cleanup, within } from '@testing-library/svelte'
import { ApiError } from '$lib/utils/errors'
import type { GpuDevice, GpuInventory } from '$lib/api'

vi.mock('$lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api')>()
  return { ...actual, getGpus: vi.fn(), refreshGpus: vi.fn(), selectGpu: vi.fn() }
})

import * as api from '$lib/api'
import GpuDevices from '$lib/components/GpuDevices.svelte'

const AMD: GpuDevice = {
  id: 'pci:0000:03:00.0', vendor: 'amd', vendor_label: 'AMD', name: 'Radeon RX 6700 XT',
  backend: 'rocm', usable: true, reason: null, vram_mb: 12288,
}
const NVIDIA: GpuDevice = {
  id: 'pci:0000:04:00.0', vendor: 'nvidia', vendor_label: 'NVIDIA', name: 'GeForce RTX 3060',
  backend: 'cuda', usable: true, reason: null, vram_mb: 8192,
}
const BROKEN: GpuDevice = {
  id: 'pci:0000:05:00.0', vendor: 'intel', vendor_label: 'Intel', name: 'Arc A380',
  backend: 'xpu', usable: false, vram_mb: null,
  reason: 'Intel GPU detected, but the installed torch has no XPU support.',
}

function inv(devices: GpuDevice[], extra: Partial<GpuInventory> = {}): GpuInventory {
  const usable = devices.filter(d => d.usable)
  return {
    devices, selected: null, default: usable[0]?.id ?? null, needs_choice: usable.length > 1,
    torch_build: 'rocm', state: 'ready', ...extra,
  }
}

describe('GpuDevices', () => {
  beforeEach(() => {
    cleanup()
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('lists devices with badge, VRAM and state, and hides the picker for one usable device', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([AMD]))
    render(GpuDevices)

    await waitFor(() => expect(screen.getByText('Radeon RX 6700 XT')).toBeTruthy())
    expect(screen.getByText('AMD ROCm')).toBeTruthy()
    expect(screen.getByText('12 GB VRAM')).toBeTruthy()
    expect(screen.getByText('Usable')).toBeTruthy()
    expect(screen.queryByRole('radio')).toBeNull()
    expect(screen.queryByText('GPU to use')).toBeNull()
  })

  it('renders the actionable reason for an unusable device', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([BROKEN], { torch_build: 'cuda' }))
    render(GpuDevices)

    await waitFor(() => expect(screen.getByText('Not usable')).toBeTruthy())
    expect(screen.getByText(/no XPU support/)).toBeTruthy()
    expect(screen.getByText('Intel XPU')).toBeTruthy()
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it('renders nothing when there is no GPU hardware', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([]))
    const { container } = render(GpuDevices)
    await waitFor(() => expect(api.getGpus).toHaveBeenCalled())
    expect(container.textContent?.trim()).toBe('')
  })

  it('shows a picker for two usable devices, saves via selectGpu and shows the restart notice', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([AMD, NVIDIA, BROKEN]))
    vi.mocked(api.selectGpu).mockResolvedValue({
      ...inv([AMD, NVIDIA, BROKEN], { selected: NVIDIA.id }), restart_required: true,
    })
    render(GpuDevices)

    const group = await screen.findByRole('group', { name: 'GPU to use' })
    const radios = within(group).getAllByRole('radio') as HTMLInputElement[]
    expect(radios).toHaveLength(2) // the unusable device is not offered
    expect(radios[0].checked).toBe(true) // default

    await fireEvent.click(radios[1])

    await waitFor(() => expect(api.selectGpu).toHaveBeenCalledWith(NVIDIA.id))
    const notice = await screen.findByText(/Restart the app/)
    expect(notice.getAttribute('role')).toBe('status')
    expect(notice.textContent).toContain('GeForce RTX 3060')
  })

  it('shows the server refusal when a selection is rejected', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([AMD, NVIDIA]))
    vi.mocked(api.selectGpu).mockRejectedValue(new ApiError(409, 'Conflict', 'GPU went away'))
    render(GpuDevices)

    const radios = (await screen.findAllByRole('radio')) as HTMLInputElement[]
    await fireEvent.click(radios[1])

    expect((await screen.findByRole('alert')).textContent).toContain('GPU went away')
    expect(screen.queryByText(/Restart the app/)).toBeNull()
  })

  it('polls while validating, then stops once ready', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getGpus)
      .mockResolvedValueOnce(inv([{ ...AMD, usable: null, reason: 'validating' }], { state: 'validating', torch_build: null }))
      .mockResolvedValueOnce(inv([{ ...AMD, usable: null, reason: 'validating' }], { state: 'validating', torch_build: null }))
      .mockResolvedValue(inv([AMD]))
    render(GpuDevices, { props: { pollMs: 2000 } })

    await vi.advanceTimersByTimeAsync(0)
    expect(screen.getByText(/Validating GPUs/)).toBeTruthy()
    expect(api.getGpus).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(api.getGpus).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.getGpus).toHaveBeenCalledTimes(3)
    expect(screen.getByText('Usable')).toBeTruthy()
    expect(screen.queryByText(/Validating GPUs/)).toBeNull()

    await vi.advanceTimersByTimeAsync(20000)
    expect(api.getGpus).toHaveBeenCalledTimes(3) // stopped
  })

  it('stops polling at the cap and offers a re-scan', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getGpus).mockResolvedValue(inv([{ ...AMD, usable: null, reason: 'validating' }], { state: 'validating' }))
    render(GpuDevices, { props: { pollMs: 1000, maxPolls: 3 } })

    await vi.advanceTimersByTimeAsync(10000)
    expect(api.getGpus).toHaveBeenCalledTimes(4) // initial + 3 polls
    expect(screen.getByText(/taking longer than expected/)).toBeTruthy()
  })

  it('clears its timer on destroy', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getGpus).mockResolvedValue(inv([AMD], { state: 'validating' }))
    const { unmount } = render(GpuDevices, { props: { pollMs: 1000 } })
    await vi.advanceTimersByTimeAsync(0)
    expect(api.getGpus).toHaveBeenCalledTimes(1)

    unmount()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.getGpus).toHaveBeenCalledTimes(1)
  })

  it('reports a timed-out check and Re-scan starts a new validation', async () => {
    vi.mocked(api.getGpus).mockResolvedValue(inv([{ ...AMD, usable: false, reason: 'PyTorch probe failed: probe timed out' }], { state: 'timed_out' }))
    vi.mocked(api.refreshGpus).mockResolvedValue(inv([AMD], { state: 'ready' }))
    render(GpuDevices)

    expect(await screen.findByText(/check timed out/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Re-scan' }))

    await waitFor(() => expect(api.refreshGpus).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('Usable')).toBeTruthy())
    expect(screen.queryByText(/check timed out/)).toBeNull()
  })
})
