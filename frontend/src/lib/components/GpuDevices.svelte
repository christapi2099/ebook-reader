<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { getGpus, refreshGpus, selectGpu, type GpuDevice, type GpuInventory } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'

  /**
   * Detected GPUs under the "This device · GPU" engine card: what the hardware
   * is, whether PyTorch can drive it (with the actionable reason when not), and,
   * only when more than one device is usable, which one the GPU engine should
   * use. The backend probes in the background, so while it answers "validating"
   * this polls (capped) and stops on any other state or when destroyed.
   */
  let {
    pollMs = 2000,
    maxPolls = 30,
  }: { pollMs?: number; maxPolls?: number } = $props()

  const BACKEND_LABEL: Record<string, string> = { cuda: 'CUDA', rocm: 'ROCm', xpu: 'XPU' }

  let inventory = $state<GpuInventory | null>(null)
  let loadError = $state<string | null>(null)
  let polling = $state(false)
  let pollsExhausted = $state(false)
  let selectError = $state<string | null>(null)
  let saving = $state(false)
  /** Name of the device just saved; drives the restart notice. */
  let savedName = $state<string | null>(null)

  let timer: ReturnType<typeof setTimeout> | null = null
  let destroyed = false
  let polls = 0

  function stopTimer() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function apply(next: GpuInventory) {
    inventory = next
    loadError = null
    stopTimer()
    if (next.state !== 'validating') {
      polling = false
      return
    }
    if (polls >= maxPolls) {
      polling = false
      pollsExhausted = true
      return
    }
    polling = true
    timer = setTimeout(poll, pollMs)
  }

  async function poll() {
    timer = null
    polls += 1
    try {
      const next = await getGpus()
      if (!destroyed) apply(next)
    } catch (e) {
      if (destroyed) return
      polling = false
      loadError = toDetailMessage(e)
    }
  }

  async function load(action: () => Promise<GpuInventory>) {
    stopTimer()
    polls = 0
    pollsExhausted = false
    try {
      const next = await action()
      if (!destroyed) apply(next)
    } catch (e) {
      if (destroyed) return
      polling = false
      loadError = toDetailMessage(e)
    }
  }

  const rescan = () => load(refreshGpus)

  const usable = $derived((inventory?.devices ?? []).filter(d => d.usable === true))
  const effectiveId = $derived(inventory?.selected ?? inventory?.default ?? null)
  const validating = $derived(inventory?.state === 'validating')

  async function choose(device: GpuDevice) {
    if (saving || effectiveId === device.id) return
    saving = true
    selectError = null
    try {
      const next = await selectGpu(device.id)
      inventory = next
      savedName = device.name
    } catch (e) {
      selectError = toDetailMessage(e)
    } finally {
      saving = false
    }
  }

  function vram(mb: number | null): string | null {
    if (mb === null) return null
    return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`
  }

  function badge(d: GpuDevice): string {
    return `${d.vendor_label} ${BACKEND_LABEL[d.backend] ?? d.backend}`
  }

  onMount(() => load(getGpus))
  onDestroy(() => {
    destroyed = true
    stopTimer()
  })
</script>

{#if inventory === null && loadError}
  <p class="mt-3 text-xs text-fg-muted" role="status">Could not read GPU devices: {loadError}</p>
{:else if inventory && (inventory.devices.length > 0 || inventory.state !== 'ready')}
  <section class="mt-4 rounded-lg border border-border p-3" aria-labelledby="gpu-devices-title">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h4 id="gpu-devices-title" class="text-xs font-semibold uppercase tracking-wide text-fg-subtle">
        Detected GPUs
      </h4>
      <button
        type="button"
        class="min-h-11 rounded-lg px-2 text-xs font-medium text-accent underline underline-offset-2 disabled:opacity-50"
        disabled={polling}
        onclick={rescan}
      >
        Re-scan
      </button>
    </div>

    <div aria-live="polite">
      {#if validating && !pollsExhausted}
        <p class="mt-1 flex items-center gap-2 text-xs text-fg-muted">
          <svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          Validating GPUs with PyTorch…
        </p>
      {:else if validating}
        <p class="mt-1 text-xs text-warning">Validation is taking longer than expected. Use Re-scan to try again.</p>
      {:else if inventory.state === 'timed_out'}
        <p class="mt-1 text-xs text-warning">The GPU check timed out (a driver may be stuck). Use Re-scan to try again.</p>
      {:else if inventory.state === 'error'}
        <p class="mt-1 text-xs text-danger">The GPU check failed. See the reasons below, then Re-scan.</p>
      {/if}
      {#if loadError}
        <p class="mt-1 text-xs text-danger">Could not read GPU devices: {loadError}</p>
      {/if}
    </div>

    <ul class="mt-2 space-y-2">
      {#each inventory.devices as device (device.id)}
        <li class="rounded-lg border border-border bg-surface p-3 text-sm" data-testid="gpu-device">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-medium text-fg">{device.name}</span>
            <span class="rounded-full bg-surface-sunken px-2 py-0.5 text-[11px] font-medium text-fg-muted">{badge(device)}</span>
            {#if vram(device.vram_mb)}
              <span class="text-xs text-fg-muted">{vram(device.vram_mb)} VRAM</span>
            {/if}
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium {device.usable === true ? 'bg-accent-soft text-fg' : device.usable === false ? 'bg-danger-soft text-danger' : 'bg-surface-sunken text-fg-muted'}">
              {device.usable === true ? 'Usable' : device.usable === false ? 'Not usable' : 'Validating…'}
            </span>
          </div>
          {#if device.usable === false && device.reason}
            <p class="mt-1 text-xs text-fg-muted">{device.reason}</p>
          {/if}
        </li>
      {/each}
    </ul>

    {#if inventory.needs_choice}
      <fieldset class="mt-3" disabled={saving}>
        <legend class="text-sm font-medium text-fg">GPU to use</legend>
        <div class="mt-2 space-y-2">
          {#each usable as device (device.id)}
            <label class="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border p-3 text-sm {effectiveId === device.id ? 'border-accent bg-accent-soft' : 'border-border bg-surface'}">
              <input
                type="radio"
                name="gpu-device"
                value={device.id}
                checked={effectiveId === device.id}
                onchange={() => choose(device)}
                class="accent-accent"
              />
              <span class="min-w-0 flex-1 text-fg">
                {device.name}
                <span class="text-xs text-fg-muted">({badge(device)}{vram(device.vram_mb) ? `, ${vram(device.vram_mb)}` : ''})</span>
              </span>
            </label>
          {/each}
        </div>
      </fieldset>
      {#if selectError}
        <p class="mt-2 text-xs text-danger" role="alert">{selectError}</p>
      {/if}
      {#if savedName}
        <p class="mt-2 text-xs text-warning" role="status">
          Saved “{savedName}”. Restart the app for the GPU engine to use it.
        </p>
      {/if}
    {/if}
  </section>
{/if}
