# Playwright Test Plan: Audio Playback + Highlight Sync

## Goal
Verify that as Kokoro TTS speaks each sentence, the corresponding sentence overlay in PDFViewer.svelte becomes highlighted in real time — and that seeking, pausing, and speed changes keep audio and highlight in sync.

## Prerequisites
- App running: backend on :8000, frontend on :5173
- A test PDF uploaded and its `book_id` known (seed via API or use a fixture book)
- `@playwright/test` installed in `frontend/`
- Playwright config: `baseURL: 'http://localhost:5173'`

## Test Setup

### Fixture: uploadTestBook
Before suite: POST a small 2-page test PDF (3–5 short sentences per page) to `/documents/upload`.
Store the returned `book_id`. Navigate to `/reader/{book_id}`.

### Helpers
- `getSentenceEl(page, index)` — returns the overlay div for sentence index N.
  Selector: `[data-sentence-index="${index}"]` or the element with `highlighted` class.
- `isHighlighted(el)` — checks for the active CSS class/style the component applies.
- `waitForHighlight(page, index, timeout=4000)` — polls until sentence N is highlighted.

---

## Test Cases

### T1 — First sentence highlights on play
**Steps:**
1. Navigate to reader page, wait for sentences to render.
2. Click the play button (MediaBar).
3. Wait up to 4s for any sentence element to gain the highlighted class.
**Assert:** At least one sentence overlay is highlighted within 4s.

### T2 — Highlight advances automatically during playback
**Steps:**
1. Start playback (T1 setup).
2. Record `index_at_t0` = currently highlighted sentence index.
3. Wait 4s.
4. Record `index_at_t4` = currently highlighted sentence index.
**Assert:** `index_at_t4 > index_at_t0` — highlight moved forward.

### T3 — Seek updates highlight to target sentence
**Steps:**
1. Start playback.
2. Wait for first highlight.
3. Click sentence overlay at index 3 (trigger seek).
4. Wait up to 3s.
**Assert:** Sentence 3 overlay becomes highlighted within 3s of click.

### T4 — Pause freezes highlight
**Steps:**
1. Start playback, wait for highlight at index N.
2. Click pause button.
3. Record highlighted index immediately after pause.
4. Wait 2s.
5. Check highlighted index again.
**Assert:** Same sentence highlighted before and after the 2s wait.

### T5 — Resume continues from paused position
**Steps:**
1. Play → pause at sentence N → wait 1s → resume.
2. Wait 3s.
**Assert:** Highlighted index advances past N (playback resumed, not restarted from 0).

### T6 — Speed change does not crash or freeze highlight
**Steps:**
1. Start playback at 1x, wait for highlight.
2. Change speed to 1.5x via speed control in MediaBar.
3. Wait 3s.
**Assert:** A sentence is still highlighted (no freeze), no JS console errors.

### T7 — Speed 1.5x highlights advance faster than 1x
**Steps:**
1. Run T2 at 1x, record sentences advanced in 4s → `delta_1x`.
2. Restart, run same at 1.5x, record `delta_1_5x`.
**Assert:** `delta_1_5x >= delta_1x` (faster speed ≥ same highlight advancement).

### T8 — Highlight is visually within the PDF canvas viewport
**Steps:**
1. Play, wait for highlight.
2. Get bounding box of the highlighted overlay element.
**Assert:** `boundingBox.y >= 0` and within page height — overlay is not off-screen or at (0,0).

### T9 — End of book clears highlight / shows stopped state
**Steps:**
1. Use a 1-sentence book. Play to completion.
2. Wait for `complete` event (poll `isPlaying` store or wait for play button to return to idle state).
**Assert:** `isPlaying` is false. No sentence is highlighted (or last sentence stays highlighted — document which behavior is expected).

### T10 — Page turn: highlight follows sentence to new PDF page
**Steps:**
1. Book has sentences spanning page 1 and page 2.
2. Play until highlight reaches last sentence on page 1.
3. Continue playing — PDFViewer should auto-scroll or page-turn to page 2.
**Assert:** Highlighted sentence is visible in the viewport (not behind current page render).

---

## Timing Approach
- Use `page.waitForFunction()` to poll the DOM for highlight class changes.
- DO NOT use fixed `page.waitForTimeout()` except as a last resort with generous bounds.
- For audio timing assertions (T7), accept ±1 sentence tolerance due to rAF jitter.

## Console Error Monitoring
Add to every test:
```ts
page.on('console', msg => {
  if (msg.type() === 'error') errors.push(msg.text())
})
// After test:
expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0)
```

## File Location
`frontend/tests/highlight-sync.spec.ts`

## Run Command
```bash
cd frontend && npx playwright test tests/highlight-sync.spec.ts --headed
```
