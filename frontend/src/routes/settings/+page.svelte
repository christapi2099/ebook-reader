<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import {
    settingsStore,
    BIONIC_MIN_WORD_LENGTH_RANGE,
    HIGHLIGHT_SWATCHES,
    type SettingsState,
    type Theme,
  } from '$lib/stores/settings'
  import {
    getCapabilities,
    getEngineState,
    getLibrary,
    setEngine,
    type EngineState,
    type SystemCapabilities,
  } from '$lib/api'
  import GpuDevices from '$lib/components/GpuDevices.svelte'
  import { toDetailMessage } from '$lib/utils/errors'
  import Switch from '$lib/ui/Switch.svelte'

  let settings: SettingsState = $state({ ...get(settingsStore) })
  const unsubscribeSettings = settingsStore.subscribe(s => { settings = s })
  onDestroy(unsubscribeSettings)

  const colorSwatches = [
    { value: HIGHLIGHT_SWATCHES[0], label: 'Amber' },
    { value: HIGHLIGHT_SWATCHES[1], label: 'Leaf' },
    { value: HIGHLIGHT_SWATCHES[2], label: 'Sky' },
    { value: HIGHLIGHT_SWATCHES[3], label: 'Rose' },
    { value: HIGHLIGHT_SWATCHES[4], label: 'Violet' },
  ]

  const themes: { value: Theme; label: string }[] = [
    { value: 'light', label: 'Light' },
    { value: 'sepia', label: 'Sepia' },
    { value: 'dark', label: 'Dark' },
  ]

  function setColor(color: string) {
    settingsStore.setHighlightColor(color)
  }

  function setTheme(theme: Theme) {
    settingsStore.setTheme(theme)
  }

  function toggleAutoscroll() {
    settingsStore.toggleAutoscroll()
  }

  function toggleHotkeys() {
    settingsStore.toggleHotkeys()
  }

  function toggleHighlight() {
    settingsStore.toggleHighlight()
  }

  function toggleBionicMode() {
    settingsStore.toggleBionicMode()
  }

  function handleBionicFixation(e: Event) {
    settingsStore.setBionicFixation(Number((e.target as HTMLInputElement).value))
  }

  function handleBionicBoldRatio(e: Event) {
    settingsStore.setBionicBoldRatio(Number((e.target as HTMLInputElement).value))
  }

  function handleBionicMinWordLength(e: Event) {
    settingsStore.setBionicMinWordLength(Number((e.target as HTMLInputElement).value))
  }

  function toggleBionicSkipCommonWords() {
    settingsStore.toggleBionicSkipCommonWords()
  }

  // ── Processing engine ──
  // Everything below is read from the backend's own probes. There is no
  // "Needs CUDA" pill and no engine card the server cannot actually run: each
  // option carries the probe's verdict, and selecting one switches the live
  // engine or reports the server's refusal. A Cloud card only ever appears if
  // the backend's own options list includes one (handoff §3.4 — no Modal
  // infrastructure exists to fake a connect flow for).

  let engineState = $state<EngineState | null>(null)
  let capabilities = $state<SystemCapabilities | null>(null)
  let engineLoading = $state(true)
  let engineError = $state<string | null>(null)
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

  /** A generic icon per engine id — the label already says what it is. */
  function engineIconPath(engineId: string): string {
    if (engineId.includes('gpu') && !engineId.includes('remote')) {
      return 'M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 20.25a48.25 48.25 0 01-8.135-.687c-1.718-.293-2.3-2.379-1.067-3.61L5 14.5'
    }
    if (engineId.includes('remote') || engineId.includes('cloud') || engineId.includes('modal')) {
      return 'M2.25 15a4.5 4.5 0 004.5 4.5H18a3.75 3.75 0 001.332-7.257 3 3 0 00-3.758-3.848 5.25 5.25 0 00-10.233 2.33A4.502 4.502 0 002.25 15z'
    }
    return 'M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25z'
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

    if (caps.remote.configured) {
      facts.push({
        label: 'Cloud · Modal',
        value: caps.remote.reachable
          ? `Configured · reachable (${caps.remote.resolved_transport ?? 'transport not reported'})`
          : 'Configured · unreachable',
      })
    }

    return facts
  })

  // ── Storage ──
  // The only real facts worth showing without fabricating counts the backend
  // never reports: how many books are indexed right now (one cheap request
  // to the endpoint every other screen already uses) plus the same engine
  // and bionic status shown above.
  let bookCount = $state<number | null>(null)
  let bookCountError = $state<string | null>(null)

  async function loadBookCount() {
    try {
      bookCount = (await getLibrary()).length
    } catch (e) {
      bookCountError = toDetailMessage(e)
    }
  }

  onMount(() => {
    loadEngineInfo()
    loadBookCount()
  })

  const sections = [
    { id: 'processing', label: 'Processing', icon: 'M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z' },
    { id: 'bionic', label: 'Bionic reading', icon: 'M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25' },
    { id: 'reading', label: 'Reading', icon: 'M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z' },
    { id: 'storage', label: 'Storage', icon: 'M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375' },
  ]
  let activeSection = $state('processing')
</script>

<div class="p-4 md:p-6 max-w-4xl">
  <div class="mb-8">
    <p class="text-xs font-semibold uppercase tracking-wide text-accent">Preferences</p>
    <h1 class="text-xl md:text-2xl font-bold text-fg mt-1">Settings</h1>
    <p class="text-sm text-fg-muted mt-2 max-w-2xl">
      Choose where books are processed, how bionic markup is generated, and what the reader
      does by default. Everything here is stored on this device.
    </p>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-[160px_1fr] gap-8">
    <nav class="flex md:flex-col flex-wrap gap-1 md:sticky md:top-6 md:self-start" aria-label="Settings sections">
      {#each sections as section (section.id)}
        <a
          href={`#${section.id}`}
          class="flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors {activeSection === section.id ? 'bg-accent-soft text-accent' : 'text-fg-muted hover:bg-surface-sunken hover:text-fg'}"
          onclick={() => (activeSection = section.id)}
        >
          <svg class="w-[18px] h-[18px] flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d={section.icon} />
          </svg>
          {section.label}
        </a>
      {/each}
    </nav>

    <div class="space-y-10 min-w-0">
      <!-- ── Processing ── -->
      <section id="processing" aria-labelledby="processing-title">
        <h2 id="processing-title" class="text-lg font-semibold text-fg">Processing engine</h2>
        <p class="mt-1 text-sm text-fg-muted max-w-2xl">
          Where a book is turned into speakable text. Whichever engine runs, the finished
          sentences are written to the same library database on this device.
        </p>

        <div class="mt-4">
          {#if engineLoading}
            <p class="text-sm text-fg-muted" role="status">Reading the engine state…</p>
          {:else if engineError}
            <div class="rounded-lg border border-danger bg-danger-soft p-4">
              <p class="text-sm text-fg" role="alert">{engineError}</p>
              <button
                type="button"
                class="mt-2 min-h-11 text-sm font-medium text-accent underline underline-offset-2"
                onclick={loadEngineInfo}
              >
                Try again
              </button>
            </div>
          {:else if engineState}
            <fieldset disabled={switchingTo !== null}>
              <legend class="sr-only">Processing engine</legend>
              <div class="space-y-3" role="radiogroup" aria-label="Processing engine">
                {#each engineState.options as option (option.id)}
                  <button
                    type="button"
                    role="radio"
                    aria-checked={engineState.active === option.id}
                    disabled={!option.available}
                    onclick={() => chooseEngine(option.id)}
                    class="w-full flex items-start gap-4 rounded-xl border p-4 text-left transition-colors {engineState.active === option.id ? 'border-accent bg-accent-soft' : 'border-border bg-surface hover:border-border-strong'} {option.available ? 'cursor-pointer' : 'opacity-60 cursor-not-allowed'}"
                  >
                    <span class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-surface-sunken text-fg-muted">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d={engineIconPath(option.id)} />
                      </svg>
                    </span>
                    <span class="min-w-0 flex-1">
                      <span class="flex flex-wrap items-center gap-2">
                        <span class="text-sm font-semibold text-fg">{option.label}</span>
                        <span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium {option.available ? 'bg-surface-sunken text-fg-muted' : 'bg-danger-soft text-danger'}">
                          {option.available
                            ? (engineState.active === option.id ? 'Live now' : 'Available')
                            : (option.reason ?? 'Unavailable')}
                        </span>
                      </span>
                    </span>
                    <span
                      class="mt-0.5 h-4 w-4 flex-shrink-0 rounded-full border-2 {engineState.active === option.id ? 'border-accent bg-accent' : 'border-border-strong'}"
                      aria-hidden="true"
                    ></span>
                  </button>
                {/each}
              </div>
            </fieldset>

            <GpuDevices />

            {#if switchingTo}
              <p class="mt-3 flex items-center gap-2 text-sm text-fg-muted" role="status">
                <svg class="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
                </svg>
                Switching to {engineLabel(switchingTo)}…
              </p>
            {/if}

            {#if engineState.selected && engineState.selected !== engineState.active}
              <p class="mt-3 text-sm text-warning">
                Saved choice "{engineLabel(engineState.selected)}" is not live — the backend is
                running {engineState.active ? engineLabel(engineState.active) : 'no engine'}.
              </p>
            {/if}

            {#if switchError}
              <p class="mt-3 text-sm text-danger" role="alert">{switchError}</p>
            {/if}

            {#if engineFacts.length > 0}
              <dl class="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm rounded-lg border border-border p-4">
                {#each engineFacts as fact (fact.label)}
                  <dt class="text-fg-muted">{fact.label}</dt>
                  <dd class="min-w-0 break-words text-fg">{fact.value}</dd>
                {/each}
              </dl>
            {/if}

            {#if capabilities?.errors.startup}
              <p class="mt-2 text-sm text-danger">Startup error: {capabilities.errors.startup}</p>
            {/if}
            {#if capabilities?.errors.remote}
              <p class="mt-2 text-sm text-fg-muted">Cloud error: {capabilities.errors.remote}</p>
            {/if}
            {#if capabilities?.local.error}
              <p class="mt-2 text-sm text-fg-muted">Torch error: {capabilities.local.error}</p>
            {/if}
          {/if}
        </div>
      </section>

      <!-- ── Bionic reading ── -->
      <section id="bionic" aria-labelledby="bionic-title" class="border-t border-border pt-8">
        <h2 id="bionic-title" class="text-lg font-semibold text-fg">Bionic reading</h2>
        <p class="mt-1 text-sm text-fg-muted max-w-2xl">
          Bold the opening letters of each word so the eye lands on an anchor and skims the
          rest.
        </p>

        <div class="mt-4 space-y-4">
          <Switch
            id="bionic-toggle"
            label="Bionic reading"
            hint="Applies the anchor styling to every chapter opened in the reader"
            checked={settings.bionicMode}
            onToggle={toggleBionicMode}
          />

          {#if settings.bionicMode}
            <div class="space-y-4 rounded-lg border border-border bg-surface-sunken p-4">
              <div>
                <label for="bionic-fixation" class="flex justify-between text-sm font-medium text-fg">
                  <span>Fixation point</span>
                  <span class="tabular-nums text-fg-muted">{settings.bionicFixation}</span>
                </label>
                <input
                  id="bionic-fixation"
                  type="range"
                  min="1"
                  max="5"
                  step="1"
                  value={settings.bionicFixation}
                  oninput={handleBionicFixation}
                  class="mt-2 w-full accent-accent"
                />
              </div>
              <div>
                <label for="bionic-ratio" class="flex justify-between text-sm font-medium text-fg">
                  <span>Bold strength</span>
                  <span class="tabular-nums text-fg-muted">{settings.bionicBoldRatio.toFixed(2)}</span>
                </label>
                <input
                  id="bionic-ratio"
                  type="range"
                  min="0.2"
                  max="0.8"
                  step="0.05"
                  value={settings.bionicBoldRatio}
                  oninput={handleBionicBoldRatio}
                  class="mt-2 w-full accent-accent"
                />
              </div>
              <div>
                <label for="bionic-min-length" class="flex justify-between text-sm font-medium text-fg">
                  <span>Minimum word length</span>
                  <span class="tabular-nums text-fg-muted">{settings.bionicMinWordLength}</span>
                </label>
                <input
                  id="bionic-min-length"
                  type="range"
                  min={BIONIC_MIN_WORD_LENGTH_RANGE.min}
                  max={BIONIC_MIN_WORD_LENGTH_RANGE.max}
                  step="1"
                  value={settings.bionicMinWordLength}
                  oninput={handleBionicMinWordLength}
                  class="mt-2 w-full accent-accent"
                />
                <p class="mt-1 text-xs text-fg-subtle">Words shorter than this are left unbolded.</p>
              </div>
              <Switch
                id="bionic-common-words-toggle"
                label="Skip short and common words"
                hint="Leave “the”, “and”, “of” and friends unbolded"
                checked={settings.bionicSkipCommonWords}
                onToggle={toggleBionicSkipCommonWords}
              />
            </div>
          {/if}
        </div>
      </section>

      <!-- ── Reading defaults ── -->
      <section id="reading" aria-labelledby="reading-title" class="border-t border-border pt-8">
        <h2 id="reading-title" class="text-lg font-semibold text-fg">Reading defaults</h2>
        <p class="mt-1 text-sm text-fg-muted max-w-2xl">
          Applied to every book you open. The reader can still override highlight and
          autoscroll per session from its own settings sheet.
        </p>

        <div class="mt-4 divide-y divide-border rounded-lg border border-border">
          <div class="flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <p class="text-sm font-medium text-fg">Theme</p>
              <p class="text-xs text-fg-muted">Sepia and dark remap the whole palette, including the reader page.</p>
            </div>
            <div class="inline-flex gap-0.5 rounded-lg bg-surface-sunken p-1" role="group" aria-label="Theme">
              {#each themes as t (t.value)}
                <button
                  type="button"
                  aria-pressed={settings.theme === t.value}
                  class="min-h-9 rounded-md px-3 text-sm font-medium transition-colors {settings.theme === t.value ? 'bg-surface text-fg shadow-1' : 'text-fg-muted hover:text-fg'}"
                  onclick={() => setTheme(t.value)}
                >
                  {t.label}
                </button>
              {/each}
            </div>
          </div>

          <div class="flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <p class="text-sm font-medium text-fg">Highlight colour</p>
              <p class="text-xs text-fg-muted">Marks the sentence being spoken.</p>
            </div>
            <div class="flex flex-wrap gap-2" role="radiogroup" aria-label="Highlight colour">
              {#each colorSwatches as swatch (swatch.value)}
                <button
                  class="h-8 w-8 rounded-full border-2 border-transparent transition-shadow {settings.highlightColor === swatch.value ? 'shadow-[0_0_0_3px_var(--surface),0_0_0_5px_var(--fg)]' : 'hover:shadow-1'}"
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

          <div class="p-4">
            <Switch
              id="highlight-toggle"
              label="Sentence highlight"
              hint="Show highlight on current sentence while reading"
              checked={settings.highlightEnabled}
              onToggle={toggleHighlight}
            />
          </div>
          <div class="p-4">
            <Switch
              id="autoscroll-toggle"
              label="Auto-scroll"
              hint="Follow current sentence automatically"
              checked={settings.autoscroll}
              onToggle={toggleAutoscroll}
            />
          </div>
          <div class="p-4">
            <Switch
              id="hotkeys-toggle"
              label="Keyboard hotkeys"
              hint="Space, arrows, B, F, Esc"
              checked={settings.hotkeysEnabled}
              onToggle={toggleHotkeys}
            />
          </div>
        </div>

        <div class="mt-4 space-y-1 rounded-lg bg-surface-sunken p-4 text-xs text-fg-muted">
          <p class="mb-1 font-medium text-fg">Hotkey reference</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">Space</kbd> Play / Pause</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">←</kbd> <kbd class="rounded bg-surface px-1 text-xs">→</kbd> Previous / Next sentence</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">↑</kbd> <kbd class="rounded bg-surface px-1 text-xs">↓</kbd> Speed up / Slow down</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">B</kbd> Bookmark current position</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">F</kbd> Open search</p>
          <p><kbd class="rounded bg-surface px-1 text-xs">Esc</kbd> Close overlays</p>
        </div>
      </section>

      <!-- ── Storage ── -->
      <section id="storage" aria-labelledby="storage-title" class="border-t border-border pt-8 pb-10">
        <h2 id="storage-title" class="text-lg font-semibold text-fg">Library database</h2>
        <p class="mt-1 text-sm text-fg-muted max-w-2xl">
          Every processed book is indexed into a local database on this device.
        </p>

        <div class="mt-4 rounded-lg border border-border p-4">
          <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt class="text-fg-muted">Books indexed</dt>
            <dd class="text-fg">
              {#if bookCount !== null}{bookCount}{:else if bookCountError}Unknown ({bookCountError}){:else}Loading…{/if}
            </dd>
            <dt class="text-fg-muted">Processing engine</dt>
            <dd class="text-fg">{engineState?.active ? engineLabel(engineState.active) : 'Unknown'}</dd>
            <dt class="text-fg-muted">Bionic markup</dt>
            <dd class="text-fg">{settings.bionicMode ? 'On' : 'Off'}</dd>
          </dl>
        </div>
      </section>
    </div>
  </div>
</div>
