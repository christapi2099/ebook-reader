# Playwright Integration Test Plan: Audio Highlight Sync (Final)

Reviewed by: DeepSeek V4 Pro, GLM 5.1, Mimo v2-flash + Exa/Firecrawl research.

---

## How the Sync Pipeline Works

```
WS: sentence_start{index, session_id}
    → binary audio chunks (Buffer)
    → sentence_end{index, duration_ms, session_id}
    → complete{session_id}

Frontend:
  sentence_start  → arm activeSessionId, set receivingSentenceIndex
  audio chunk     → scheduleChunk(bytes, idx)
                     → decodeAudioData → AudioBufferSourceNode.start(startAt)
                     → sentenceTimings.set(idx, startAt)
  rAF loop        → poll ctx.currentTime → find max sentenceTimings[idx] <= now
                     → update store.currentIndex
  onended         → fallback advance if rAF missed short sentence
  PDFViewer $effect → O(1) class swap on sentenceElements Map

Guards:
  session_id filter  — drops stale sentence_start / complete
  activeSessionId    — gates binary chunks until matching sentence_start arms it
  generation counter — captured in scheduleChunk closure, blocks stale onended
```

---

## Required PDFViewer Change

**GLM finding:** `bg-yellow-200/60` compiles to an opaque hash in Tailwind v4 — asserting
the literal class name breaks silently.

Add a `data-highlighted` attribute to the overlay div in `PDFViewer.svelte`:

```ts
// In drawHighlights(), on the active sentence div:
div.dataset.highlighted = s.index === currentIndex ? 'true' : 'false'

// In the $effect class-swap:
curr?.setAttribute('data-highlighted', 'true')
prev?.setAttribute('data-highlighted', 'false')
```

All tests assert `[data-index="N"][data-highlighted="true"]` — no CSS class dependency.

---

## Mock Strategy

### 1. `page.clock` (replaces `__mockAudioTime`)

**Mimo/DeepSeek/GLM finding:** advancing a raw variable doesn't tick rAF loops.
Playwright's native clock mocks `requestAnimationFrame`, `AudioContext.currentTime`,
`performance.now`, and `Date` together.

```ts
await page.clock.install()           // before page.goto
await page.clock.tick(200)           // advance 200ms + fires pending rAF callbacks
// NOT page.clock.fastForward() — known bug: rAF callbacks don't fire (GH #37635)
```

### 2. AudioContext mock via `page.addInitScript`

**DeepSeek finding:** mock needs controllable `AudioBufferSourceNode` with manually
fireable `onended`, so the generation guard test isn't a no-op.

**Mimo finding:** `addInitScript` args must be serializable — inline mock as a string.

**Mimo research finding:** AudioContext starts in `running` state in Playwright
(not `suspended`), so no `resume()` handling needed.

```ts
// audio-context-mock.ts — inlined string passed to addInitScript
export const AUDIO_CONTEXT_MOCK = `
  window.__mockNodes = []

  class MockAudioBufferSourceNode {
    constructor() {
      this.playbackRate = { value: 1.0 }
      this.onended = null
      window.__mockNodes.push(this)
    }
    connect() {}
    start(when) { this._scheduledAt = when }
    stop() { this._stopped = true }
    fireEnded() { this.onended?.() }
  }

  class MockAudioBuffer {
    constructor(duration) { this.duration = duration }
  }

  window.AudioContext = class {
    get currentTime() { return window.__clockTime ?? 0 }
    get state() { return 'running' }
    createBufferSource() { return new MockAudioBufferSourceNode() }
    decodeAudioData(buf) {
      return Promise.resolve(new MockAudioBuffer(0.1))
    }
    resume() { return Promise.resolve() }
    close() { return Promise.resolve() }
  }
`
// Register: await page.addInitScript(AUDIO_CONTEXT_MOCK)
// page.clock.tick() updates window.__clockTime automatically
```

### 3. WebSocket mock via `page.routeWebSocket`

**Mimo research finding:** `onMessage()` stops auto-forwarding — re-send any message
not intentionally intercepted. Binary chunks use `Buffer`, not string.

```ts
// ws-driver.ts
export class WsDriver {
  private ws: WebSocketRoute | null = null
  capturedActions: Record<string, unknown>[] = []

  async install(page: Page, bookId: string) {
    await page.routeWebSocket(
      `ws://localhost:8000/ws/tts/${bookId}`,
      (ws) => {
        this.ws = ws
        ws.onMessage((msg) => {
          if (typeof msg === 'string') {
            this.capturedActions.push(JSON.parse(msg))
          }
          // binary chunks from client → server not expected; drop
        })
      }
    )
  }

  sendSentenceStart(idx: number, sid: number) {
    this.ws!.send(JSON.stringify({ type: 'sentence_start', index: idx, session_id: sid }))
  }

  sendAudioChunk(bytes: Buffer) {
    this.ws!.send(bytes)
  }

  sendSentenceEnd(idx: number, durationMs: number, sid: number) {
    this.ws!.send(JSON.stringify({ type: 'sentence_end', index: idx, duration_ms: durationMs, session_id: sid }))
  }

  sendComplete(sid: number) {
    this.ws!.send(JSON.stringify({ type: 'complete', session_id: sid }))
  }

  lastAction() { return this.capturedActions.at(-1) }
  actionsOf(type: string) { return this.capturedActions.filter(a => a.action === type) }
}
```

### 4. HTTP mock via `page.route`

```ts
await page.route('**/documents/*/sentences', r =>
  r.fulfill({ json: MOCK_SENTENCES }))
await page.route('**/library/*/progress', r =>
  r.fulfill({ json: { sentence_index: 0 } }))
```

---

## Constants

```ts
const IDX  = { first: 0, second: 1, third: 2, clicked: 3, stale: 5, last: 4 }
const SID  = { first: 1, second: 2, stale: 99 }
const TICK = { sentence: 200, seek: 100 }  // ms to advance clock per sentence
```

---

## File Structure

```
frontend/
  playwright.config.ts
  tests/
    fixtures/
      mock-data.ts              # 5 typed Sentences across 2 pages
      audio-context-mock.ts     # AUDIO_CONTEXT_MOCK inlined string
      ws-driver.ts              # WsDriver class
    highlight-sync.spec.ts      # all tests
```

`playwright.config.ts`:
```ts
export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://localhost:5173' },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
  },
})
```

---

## Test Cases

Every test includes console error monitoring:
```ts
const errors: string[] = []
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
// afterEach: expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0)
```

---

### T1 — Initial highlight on sentence 0 (no play)

Sentence 0 is highlighted by default via `currentIndex: 0` in store init — this is a
separate code path from the rAF loop (GLM: Test 1 was incoherent without this clarification).

```
Assert: [data-index="0"][data-highlighted="true"] exists on page load
Assert: no other [data-highlighted="true"] exists
```

---

### T2 — Highlight advances and previous clears

```
Act:    Click play
        driver.sendSentenceStart(IDX.second, SID.first)
        driver.sendAudioChunk(Buffer.alloc(1024))
        await page.clock.tick(TICK.sentence)
Assert: [data-index="1"][data-highlighted="true"]
Assert: [data-index="0"][data-highlighted="false"]   ← GLM: must verify removal
```

---

### T3 — Stale session_start rejected

```
Act:    Click play   (captures sid=1)
        Click play   (captures sid=2)
        driver.sendSentenceStart(IDX.stale, SID.stale)
        await page.clock.tick(TICK.sentence)
Assert: [data-index="5"][data-highlighted="true"] never (waitForFunction with timeout)
```

---

### T4 — Stale binary chunks and stale complete rejected (GLM: new)

```
Act:    Click play (sid=1) → click play (sid=2, current)
        driver.sendAudioChunk(Buffer.alloc(1024))  // untagged, no prior sentence_start for sid=2
        driver.sendComplete(SID.stale)
Assert: isPlaying still true (play button not visible)
Assert: no highlight change
```

---

### T5 — Pause freezes highlight

```
Act:    Play → driver.sendSentenceStart(IDX.second, SID.first) + chunk + tick
        Wait for [data-index="1"][data-highlighted="true"]
        Click pause
        driver.sendSentenceStart(IDX.third, SID.first)
        await page.clock.tick(TICK.sentence)
Assert: [data-index="2"][data-highlighted="true"] never
Assert: [data-index="1"][data-highlighted="true"] still
```

---

### T6 — Resume continues from paused index (Plan B: T5)

```
Act:    Play → pause at IDX.second → wait 500ms → click play (resume)
        driver.sendSentenceStart(IDX.third, SID.second) + chunk + tick
Assert: WS captured play action with from_index === IDX.second
Assert: [data-index="2"][data-highlighted="true"]
```

---

### T7 — Sentence click triggers seek and highlight jumps (GLM: full seek integration)

```
Act:    Click [data-index="3"] overlay
        driver.sendSentenceStart(IDX.clicked, SID.first) + chunk + tick
Assert: driver.actionsOf('seek')[0].to_index === IDX.clicked
Assert: [data-index="3"][data-highlighted="true"]
```

---

### T8 — complete stops playback; highlight persists on last sentence (GLM: new)

```
Act:    Play → driver.sendSentenceStart(IDX.last, SID.first) + chunk + tick
        driver.sendComplete(SID.first)
Assert: aria-label="Play" button visible (isPlaying=false)
Assert: [data-index="4"][data-highlighted="true"]  ← last sentence stays highlighted
```

---

### T9 — Generation guard: stale onended does not clobber seek target (DeepSeek: T7)

```
Act:    Play (gen=1) → seek to IDX.clicked (gen=2, resets generation)
        Manually fire: await page.evaluate(() => window.__mockNodes[0]?.fireEnded())
Assert: [data-index="3"][data-highlighted="true"] (seek target unchanged)
Assert: NOT [data-index="0"][data-highlighted="true"]
```

---

### T10 — Rapid back-to-back sentence_starts in one rAF cycle (GLM: new)

```
Act:    Play
        driver.sendSentenceStart(0, SID.first) + chunk
        driver.sendSentenceStart(1, SID.first) + chunk   // before any tick
        driver.sendSentenceStart(2, SID.first) + chunk
        await page.clock.tick(TICK.sentence * 3)
Assert: [data-index="2"][data-highlighted="true"]  ← advanced to latest
Assert: [data-index="0"][data-highlighted="false"]
```

---

### T11 — Speed change creates new session, clears old timings (GLM + Mimo: new)

```
Act:    Play (sid=1) → highlight at IDX.second
        Click 1.5x speed button
Assert: WS captured new play action with speed === 1.5
        driver.sendSentenceStart(IDX.third, SID.second) + chunk + tick
Assert: [data-index="2"][data-highlighted="true"]
Assert: WS play action has correct speed param for all speeds: [0.75, 1.5, 2.0, 3.0]
```

---

### T12 — decodeAudioData rejection does not advance highlight (DeepSeek + Mimo: new)

```
Setup:  Override mock before play: window.AudioContext decodeAudioData rejects
Act:    Play → driver.sendSentenceStart(IDX.second, SID.first) + chunk + tick
Assert: [data-index="1"][data-highlighted="true"] never
Assert: [data-index="0"][data-highlighted="true"] still (or no change)
Assert: no console errors (pipeline swallows the decode error gracefully)
```

---

### T13 — WS disconnect mid-playback (DeepSeek: new)

```
Act:    Play → highlight at IDX.second
        Close the WS route: driver forcefully close
        await page.clock.tick(500)
Assert: highlight freezes (no further advancement)
Assert: no uncaught JS errors
```

---

### T14 — Highlight overlays within PDF canvas bounds (Plan B: T8)

```
Act:    Play → wait for any [data-highlighted="true"]
        el = page.locator('[data-highlighted="true"]')
Assert: boundingBox.y >= 0
Assert: boundingBox.y < page wrapper height
Assert: boundingBox.x >= 0
```

---

### T15 — Cross-page: highlight follows sentence to page 2 (Plan B: T10)

```
Setup:  MOCK_SENTENCES includes sentences on page 0 and page 1
Act:    Play → advance highlight to last sentence on page 0
        driver.sendSentenceStart(first_sentence_on_page_1, SID.first) + chunk + tick
Assert: [data-index="N"][data-highlighted="true"] where N is on page 1
Assert: overlay is visible in viewport (not behind unrendered page)
```

---

## Edge Cases Noted (not in initial 15, add if time permits)

- **`[data-index]` missing**: sentence on a PDF page not yet rendered by PDF.js — `$effect`
  class swap silently skips. Add guard in PDFViewer `$effect`: `curr?.setAttribute(...)`.
- **Out-of-order WS messages**: `sentence_start` after its chunks — session_id filter
  handles session isolation but sequence within session is undefined.
- **Seek to out-of-range index**: `readerStore.seek` clamps via `Math.min`; no test needed
  unless the WS message sends the unclamped value.

---

## Summary of Changes from Original Plan

| Area | Original | Final |
|---|---|---|
| Time control | `window.__mockAudioTime` | `page.clock.tick()` |
| Highlight selector | `bg-yellow-200/60` CSS class | `data-highlighted="true"` attribute |
| AudioContext mock | `decodeAudioData` resolves, no node control | Mock `AudioBufferSourceNode` with `fireEnded()` |
| `addInitScript` format | Object passed as arg | Inlined string (serialization constraint) |
| Test count | 7 | 15 core + edge cases |
| Console monitoring | Not included | Every test |
| Binary WS chunks | Not in WsDriver | `sendAudioChunk(Buffer)` |
| Speed testing | Not covered | T11 covers all speeds 0.5x–3.0x |
| Cross-session guards | T3 only | T3 + T4 (binary chunks + complete) |
