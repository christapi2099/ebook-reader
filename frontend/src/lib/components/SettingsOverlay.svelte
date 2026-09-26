<script lang="ts">
  import { onMount } from 'svelte'
  import { get } from 'svelte/store'
  import {
    settingsStore,
    BIONIC_MIN_WORD_LENGTH_RANGE,
    type SettingsState,
  } from '$lib/stores/settings'
  import {
    getCapabilities,
    getEngineState,
    setEngine,
    type EngineState,
    type SystemCapabilities,
  } from '$lib/api'
  import { toDetailMessage } from '$lib/utils/errors'
  import Dialog from '$lib/ui/Dialog.svelte'
  import Switch from '$lib/ui/Switch.svelte'

  let { onClose }: { onClose: () => void } = $props()

  let settings: SettingsState = $state({ ...get(settingsStore) })

  const colorSwatches = [
    { value: '#FCD34D', label: 'Honey' },
    { value: '#C4B5FD', label: 'Lavender' },
    { value: '#6EE7B7', label: 'Mint' },
    { value: '#F9A8D4', label: 'Rose' },
    { value: '#7DD3FC', label: 'Sky' },
    { value: '#FCA5A5', label: 'Coral' },
    { value: '#FDBA74', label: 'Peach' },
    { value: '#67E8F9', label: 'Aqua' },
  ]

  function setColor(color: string) {
    settings.highlightColor = color
    settingsStore.setHighlightColor(color)
  }

  function toggleAutoscroll() {
    settings.autoscroll = !settings.autoscroll
    settingsStore.toggleAutoscroll()
  }

  function toggleHotkeys() {
    settings.hotkeysEnabled = !settings.hotkeysEnabled
    settingsStore.toggleHotkeys()
  }

  function toggleHighlight() {
    settings.highlightEnabled = !settings.highlightEnabled
    settingsStore.toggleHighlight()
  }

  function toggleBionicMode() {
    settings.bionicMode = !settings.bionicMode
    settingsStore.toggleBionicMode()
  }

  function handleBionicFixation(e: Event) {
    const val = Number((e.target as HTMLInputElement).value)
    settings.bionicFixation = val
    settingsStore.setBionicFixation(val)
  }

  function handleBionicBoldRatio(e: Event) {
    const val = Number((e.target as HTMLInputElement).value)
    settings.bionicBoldRatio = val
    settingsStore.setBionicBoldRatio(val)
  }

  function handleBionicMinWordLength(e: Event) {
    const val = Number((e.target as HTMLInputElement).value)
    settings.bionicMinWordLength = val
    settingsStore.setBionicMinWordLength(val)
  }

  function toggleBionicSkipCommonWords() {
    settings.bionicSkipCommonWords = !settings.bionicSkipCommonWords
    settingsStore.toggleBionicSkipCommonWords()
  }

  // ── Processing engine ──
  // Everything below is read from the backend's own probes. There is no
  // "Needs CUDA" pill and no engine card the server cannot actually run: each
  // option carries the probe's verdict, and selecting one switches the live
  // engine or reports the server's refusal.

  let engineState = $state<EngineState | null>(null)
  let capabilities = $state<SystemCapabilities | null>(null)
  let engineLoading = $state(true)
  /** Set when the probe could not be read at all; there is nothing to show. */
  let engineError = $state<string | null>(null)
  /** Set when a switch was refused; the list stays up and explains why. */
  let switchError = $state<string | null>(null)
  let switchingTo = $state<string | null>(null)

  async function loadEngineInfo() {
    engineLoading = true
    engineError = null
    try {
      const [state, caps] = await Promise.all([getEngineState(), getCapabilities()])
      engineState = state
      capabilities = caps
    } catch (e) {
      engineError = toDetailMessage(e)
    } finally {
      engineLoading = false
    }
  }

  async function chooseEngine(engineId: string) {
    if (switchingTo || engineState?.active === engineId) return
    // The radio is disabled for an unavailable engine; this keeps a programmatic
    // click from asking the server for something the probe already ruled out.
    if (!engineState?.options.find(o => o.id === engineId)?.available) return
    switchingTo = engineId
    switchError = null
    try {
      engineState = await setEngine(engineId)
      capabilities = await getCapabilities().catch(() => capabilities)
    } catch (e) {
      switchError = toDetailMessage(e)
    } finally {
      switchingTo = null
    }
  }

  function engineLabel(engineId: string): string {
    return engineState?.options.find(o => o.id === engineId)?.label ?? engineId
  }

  /** Only facts the probe returned; a missing value is reported as missing. */
  const engineFacts = $derived.by(() => {
    const caps = capabilities
    if (!caps) return [] as Array<{ label: string; value: string }>
    const facts: Array<{ label: string; value: string }> = [
      { label: 'Synthesis', value: caps.synthesis_available ? 'Available' : 'Not available' },
    ]

    if (caps.active_backend === 'remote') {
      facts.push({
        label: 'Device in use',
        value: `Remote · ${caps.remote.resolved_transport ?? 'transport not reported'}`,
      })
    } else {
      facts.push({ label: 'Device in use', value: caps.local.device_in_use ?? 'none' })
    }

    facts.push({ label: 'Sample rate', value: `${caps.sample_rate} Hz` })

    if (caps.local.gpu_name) {
      facts.push({ label: 'GPU seen by torch', value: caps.local.gpu_name })
    } else {
      facts.push({ label: 'CUDA available', value: caps.local.cuda_available ? 'yes' : 'no' })
    }

    if (caps.local.torch_version) {
      facts.push({
        label: 'PyTorch',
        value: caps.local.torch_cuda_version
          ? `${caps.local.torch_version} (cuda ${caps.local.torch_cuda_version})`
          : caps.local.torch_version,
      })
    }

    facts.push({
      label: 'Cloud · Modal',
      value: !caps.remote.configured
        ? 'Not configured'
        : caps.remote.reachable
          ? `Configured · reachable (${caps.remote.resolved_transport ?? 'transport not reported'})`
          : 'Configured · unreachable',
    })

    return facts
  })

  onMount(loadEngineInfo)
</script>

<Dialog titleId="settings-dialog-title" {onClose} closeOnBackdrop class="p-4">
  <div class="max-h-[calc(100vh-2rem)] w-full max-w-sm overflow-y-auto rounded-xl border border-border bg-surface p-6 shadow-3">
    <div class="mb-4 flex items-center justify-between">
      <h2 id="settings-dialog-title" class="text-lg font-bold text-fg">Settings</h2>
      <button
        class="flex h-11 w-11 items-center justify-center rounded-lg text-fg-muted transition-colors hover:text-fg"
        onclick={onClose}
        aria-label="Close settings"
      >
        <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <div class="space-y-5">
      <div>
        <span id="color-label" class="mb-2 block text-sm font-medium text-fg">Highlight Color</span>
        <div class="flex flex-wrap gap-2" role="radiogroup" aria-labelledby="color-label">
          {#each colorSwatches as swatch}
            <button
              class="h-8 w-8 rounded-full border-2 transition-all {settings.highlightColor === swatch.value ? 'border-fg scale-110' : 'border-transparent hover:scale-105'}"
              style="background-color: {swatch.value}"
              title={swatch.label}
              aria-label={swatch.label}
              aria-checked={settings.highlightColor === swatch.value}
              onclick={() => setColor(swatch.value)}
              role="radio"
            ></button>
          {/each}
        </div>
      </div>

      <Switch
        id="highlight-toggle"
        label="Sentence Highlight"
        hint="Show highlight on current sentence while reading"
        checked={settings.highlightEnabled}
        onToggle={toggleHighlight}
      />
      <Switch
        id="autoscroll-toggle"
        label="Auto-Scroll"
        hint="Follow current sentence automatically"
        checked={settings.autoscroll}
        onToggle={toggleAutoscroll}
      />
      <Switch
        id="hotkeys-toggle"
        label="Keyboard Hotkeys"
        hint="Space, arrows, B, F, Esc"
        checked={settings.hotkeysEnabled}
        onToggle={toggleHotkeys}
      />
      <Switch
        id="bionic-toggle"
        label="Bionic Reading"
        hint="Bold initial letters to guide eye movement"
        checked={settings.bionicMode}
        onToggle={toggleBionicMode}
      />

      {#if settings.bionicMode}
        <div class="space-y-4 rounded-lg border border-border bg-surface-sunken p-3">
          <div>
            <label for="bionic-fixation" class="block text-xs font-medium text-fg">
              Fixation point: {settings.bionicFixation}
            </label>
            <input
              id="bionic-fixation"
              type="range"
              min="1"
              max="5"
              step="1"
              value={settings.bionicFixation}
              oninput={handleBionicFixation}
              class="mt-1 w-full accent-accent"
            />
            <div class="flex justify-between px-0.5 text-xs text-fg-subtle">
              <span>More bold</span>
              <span>Less bold</span>
            </div>
          </div>
          <div>
            <label for="bionic-ratio" class="block text-xs font-medium text-fg">
              Bold strength: {settings.bionicBoldRatio.toFixed(2)}
            </label>
            <input
              id="bionic-ratio"
              type="range"
              min="0.2"
              max="0.8"
              step="0.05"
              value={settings.bionicBoldRatio}
              oninput={handleBionicBoldRatio}
              class="mt-1 w-full accent-accent"
            />
            <div class="flex justify-between px-0.5 text-xs text-fg-subtle">
              <span>Light</span>
              <span>Heavy</span>
            </div>
          </div>
          <div>
            <label for="bionic-min-length" class="block text-xs font-medium text-fg">
              Minimum word length: {settings.bionicMinWordLength}
            </label>
            <input
              id="bionic-min-length"
              type="range"
              min={BIONIC_MIN_WORD_LENGTH_RANGE.min}
              max={BIONIC_MIN_WORD_LENGTH_RANGE.max}
              step="1"
              value={settings.bionicMinWordLength}
              oninput={handleBionicMinWordLength}
              class="mt-1 w-full accent-accent"
            />
            <p class="text-xs text-fg-subtle">Words shorter than this are left unbolded.</p>
          </div>
          <Switch
            id="bionic-common-words-toggle"
            label="Skip Common Words"
            hint="Leave “the”, “and”, “of” and friends unbolded"
            checked={settings.bionicSkipCommonWords}
            onToggle={toggleBionicSkipCommonWords}
          />
        </div>
      {/if}

      <section class="rounded-lg border border-border p-3" aria-labelledby="engine-section-title">
        <h3 id="engine-section-title" class="text-sm font-medium text-fg">Processing Engine</h3>
        <p class="mb-2 text-xs text-fg-muted">
          Which synthesizer reads your books. Switching takes a while — a model load,
          or a cloud GPU boot.
        </p>

        {#if engineLoading}
          <p class="text-xs text-fg-muted" role="status">Reading the engine state…</p>
        {:else if engineError}
          <div class="rounded-lg border border-danger bg-danger-soft p-3">
            <p class="text-xs text-fg" role="alert">{engineError}</p>
            <button
              type="button"
              class="mt-1 min-h-11 text-xs font-medium text-accent underline underline-offset-2"
              onclick={loadEngineInfo}
            >
              Try again
            </button>
          </div>
        {:else if engineState}
          <fieldset disabled={switchingTo !== null}>
            <legend class="sr-only">Processing engine</legend>
            <div class="space-y-2">
              {#each engineState.options as option (option.id)}
                <label
                  class="flex min-h-11 items-start gap-3 rounded-lg border border-border p-3 {engineState.active === option.id ? 'border-accent bg-accent-soft' : 'bg-surface'} {option.available ? 'cursor-pointer' : 'opacity-70'}"
                >
                  <input
                    type="radio"
                    name="processing-engine"
                    class="mt-0.5 accent-accent"
                    checked={engineState.active === option.id}
                    disabled={!option.available}
                    onchange={() => chooseEngine(option.id)}
                  />
                  <span class="min-w-0">
                    <span class="block text-sm font-medium text-fg">{option.label}</span>
                    {#if option.available}
                      <span class="block text-xs text-fg-muted">
                        {engineState.active === option.id ? 'Live now' : 'Available'}
                      </span>
                    {:else}
                      <span class="block text-xs text-danger">{option.reason ?? 'Unavailable'}</span>
                    {/if}
                  </span>
                </label>
              {/each}
            </div>
          </fieldset>

          {#if switchingTo}
            <p class="mt-2 flex items-center gap-2 text-xs text-fg-muted" role="status">
              <svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
              </svg>
              Switching to {engineLabel(switchingTo)}…
            </p>
          {/if}

          {#if engineState.selected && engineState.selected !== engineState.active}
            <p class="mt-2 text-xs text-warning">
              Saved choice “{engineLabel(engineState.selected)}” is not live — the backend is
              running {engineState.active ? engineLabel(engineState.active) : 'no engine'}.
            </p>
          {/if}

          {#if switchError}
            <p class="mt-2 text-xs text-danger" role="alert">{switchError}</p>
          {/if}

          {#if engineFacts.length > 0}
            <dl class="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {#each engineFacts as fact (fact.label)}
                <dt class="text-fg-muted">{fact.label}</dt>
                <dd class="min-w-0 break-words text-fg">{fact.value}</dd>
              {/each}
            </dl>
          {/if}

          {#if capabilities?.errors.startup}
            <p class="mt-2 text-xs text-danger">Startup error: {capabilities.errors.startup}</p>
          {/if}
          {#if capabilities?.errors.remote}
            <p class="mt-2 text-xs text-fg-muted">Cloud error: {capabilities.errors.remote}</p>
          {/if}
          {#if capabilities?.local.error}
            <p class="mt-2 text-xs text-fg-muted">Torch error: {capabilities.local.error}</p>
          {/if}
          {#if capabilities && !capabilities.remote.configured && capabilities.remote.error}
            <p class="mt-2 text-xs text-fg-muted">{capabilities.remote.error}</p>
          {/if}
        {/if}
      </section>

      <div class="space-y-1 rounded-lg bg-surface-sunken p-3 text-xs text-fg-muted">
        <p class="mb-1 font-medium text-fg">Hotkey Reference</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">Space</kbd> Play / Pause</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">←</kbd> <kbd class="rounded bg-surface px-1 text-xs">→</kbd> Previous / Next sentence</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">↑</kbd> <kbd class="rounded bg-surface px-1 text-xs">↓</kbd> Speed up / Slow down</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">B</kbd> Bookmark current position</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">F</kbd> Open search</p>
        <p><kbd class="rounded bg-surface px-1 text-xs">Esc</kbd> Close overlays</p>
      </div>
    </div>
  </div>
</Dialog>
