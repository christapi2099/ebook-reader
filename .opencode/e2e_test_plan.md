# E2E Playwright Test Plan — 4 Recent Changes

## Reference: Existing Patterns

All tests follow the style in `highlight-sync.spec.ts`:
- `beforeEach`: `page.clock.install()`, `page.addInitScript(AUDIO_CONTEXT_MOCK)`, route mocks, `driver.install()`, `page.goto('/reader/test-book')`
- `afterEach`: assert zero console errors
- Clock: `page.clock.runFor(ms)` to advance virtual time
- WsDriver: `sendSentenceStart()`, `sendAudioChunk()`, `sendSentenceEnd()`, `sendComplete()`
- DOM queries: `page.locator(selector)`, `page.evaluate(() => ...)`
- Selectors: `data-*` attributes (e.g. `[data-index="0"]`, `[data-highlighted="true"]`, `[data-word-index]`, `[data-overlay]`)

---

## Change 1 — Word-level highlighting (PDFViewer)

**File:** `frontend/tests/highlight-sync.spec.ts` — extend existing `Word-level highlighting` describe block

**mock-data.ts changes:** None needed — `MOCK_SENTENCES` already has `words` arrays on sentences 0 and 1.

**WsDriver changes:** None needed — `sendSentenceEnd()` already accepts `wordTimestamps` parameter.

### Test 1.1: `word_divs_rendered_for_sentences_with_words` (ALREADY EXISTS)
- Existing test at line 282 — no changes needed.

### Test 1.2: `current_word_div_gets_background_color` (ALREADY EXISTS)
- Existing test at line 290 — no changes needed.

### Test 1.3: `word_highlight_clears_when_index_moves_to_next_word`
- **Assert:** After `sendSentenceEnd()` with 2 word_timestamps, when the rAF tick advances `currentWordIndex` to 0, word-0 div has `backgroundColor` set. When `currentWordIndex` advances to 1 (next rAF tick), word-0's `backgroundColor` is cleared and word-1's is set.
- **Interactions:**
  1. `playBtn().click()`
  2. `driver.sendSentenceStart(0, SID.first)`
  3. `driver.sendAudioChunk(MOCK_AUDIO_CHUNK)`
  4. `driver.sendSentenceEnd(0, SID.first, [{word:'Sentence', start:0.1, end:1.5}, {word:'0.', start:1.6, end:2.0}])`
  5. `page.clock.runFor(TICK.sentence)` — rAF fires, word-0 highlighted
  6. `page.evaluate()` — read `backgroundColor` of `[data-word-index="0"][data-sentence-index="0"]` → expect non-empty
  7. `page.clock.runFor(1500)` — advance time past word-0 start, rAF picks up word-1
  8. `page.evaluate()` — word-0 `backgroundColor` is `''`, word-1 `backgroundColor` is non-empty

### Test 1.4: `word_highlight_clears_on_pause`
- **Assert:** Word-level backgroundColor clears when pause is pressed.
- **Interactions:**
  1. `playBtn().click()`
  2. `driver.sendSentenceStart(0, SID.first)`
  3. `driver.sendAudioChunk(MOCK_AUDIO_CHUNK)`
  4. `driver.sendSentenceEnd(0, SID.first, [{word:'Sentence', start:0.1, end:1.5}, {word:'0.', start:1.6, end:2.0}])`
  5. `page.clock.runFor(TICK.sentence)` — word highlighted
  6. `pauseBtn().click()`
  7. `page.evaluate()` — all `[data-word-index]` divs have `backgroundColor === ''`

### Test 1.5: `word_highlight_clears_on_seek_to_different_sentence`
- **Assert:** When seeking from sentence 0 to sentence 3, word highlights on sentence 0 are cleared.
- **Interactions:**
  1. `playBtn().click()`
  2. `driver.sendSentenceStart(0, SID.first)`
  3. `driver.sendAudioChunk(MOCK_AUDIO_CHUNK)`
  4. `driver.sendSentenceEnd(0, SID.first, [{word:'Sentence', start:0.1, end:1.5}])`
  5. `page.clock.runFor(TICK.sentence)`
  6. Click sentence 3 overlay → seek
  7. `page.evaluate()` — `[data-word-index][data-sentence-index="0"]` divs have `backgroundColor === ''`

### Test 1.6: `sentences_without_words_have_no_word_divs`
- **Assert:** Sentences with empty `words[]` array (indices 2–5) produce zero `[data-word-index]` divs with their sentence-index.
- **Interactions:**
  1. Page loads, no play needed
  2. `page.locator('[data-word-index][data-sentence-index="2"]').count()` → 0
  3. `page.locator('[data-word-index][data-sentence-index="3"]').count()` → 0

### Test 1.7: `word_divs_are_positioned_by_scaled_bbox`
- **Assert:** Word div `left`, `top`, `width`, `height` match `bbox * effectiveScale`.
- **Interactions:**
  1. Page loads
  2. `page.evaluate()` — get computed style of `[data-word-index="0"][data-sentence-index="0"]`
  3. Sentence 0 word-0 has bbox `{x0:50, y0:100, x1:150, y1:120}`, SCALE=1.5
  4. Expect `left === '75px'`, `top === '150px'`, `width === '150px'`, `height === '30px'`

---

## Change 2 — 1-word sentence bbox fix (backend)

**No E2E test needed.** This is a backend-only extraction change in `sentence_bbox()`. Covered by pytest unit tests.

---

## Change 3 — Search fill color (PDFViewer)

**File:** `frontend/tests/highlight-sync.spec.ts` — unskip and rewrite the existing `Search highlight styling` describe block

**mock-data.ts changes:** None needed.

**WsDriver changes:** None needed.

**Key selectors:**
- Open search: `page.getByRole('button', { name: 'Search' })` (TopToolbar) or `page.keyboard.press('f')`
- Search input: `page.getByPlaceholder('Search in book…')`
- Search close: `page.getByPlaceholder('Search in book…')` → type empty or click the X button in SearchOverlay
- Sentence overlay divs: `[data-index="${i}"]`

### Test 3.1: `search_matches_use_fill_not_outline` (UNSKIP existing)
- **Assert:** When search finds matches, matched sentence divs have `backgroundColor` set (not `outline` or `border`).
- **Interactions:**
  1. `page.getByRole('button', { name: 'Search' }).click()` — opens SearchOverlay
  2. `page.getByPlaceholder('Search in book…').fill('Sentence 0')` — matches sentence 0
  3. `page.evaluate()` — get `[data-index="0"]` style
  4. Assert `backgroundColor` matches `SEARCH_MATCH_COLOR` (`rgba(134,239,172,0.4)`)
  5. Assert `outline` is empty/none
  6. Assert `border` is default (no colored border)

### Test 3.2: `search_current_match_is_brighter_than_others` (UNSKIP existing)
- **Assert:** The current search match uses `SEARCH_CURRENT_COLOR` (`rgba(59,130,246,0.35)`), other matches use `SEARCH_MATCH_COLOR` (`rgba(134,239,172,0.4)`).
- **Interactions:**
  1. Click Search button
  2. Type `'Sentence'` — matches all 6 sentences (sentences 0–5 all contain "Sentence")
  3. First match (index 0) is current → `page.evaluate()` on `[data-index="0"]` → expect `backgroundColor` contains `59,130,246` (blue current color)
  4. `page.evaluate()` on `[data-index="1"]` → expect `backgroundColor` contains `134,239,172` (green match color)
  5. Click next-match button in SearchOverlay (the chevron-down button)
  6. Now index 1 is current → `page.evaluate()` on `[data-index="0"]` → expect `backgroundColor` changed to green, `[data-index="1"]` → expect blue

### Test 3.3: `search_fill_clears_when_search_closed`
- **Assert:** Closing the search overlay clears `backgroundColor` on all previously matched sentence divs.
- **Interactions:**
  1. Click Search button
  2. Type `'Sentence 0'` — match found
  3. Verify `[data-index="0"]` has non-empty `backgroundColor`
  4. Press Escape (closes search, `searchOpen=false`, `searchMatches=[]`)
  5. `page.evaluate()` on `[data-index="0"]` → expect `backgroundColor === ''`

### Test 3.4: `search_fill_does_not_interfere_with_playback_highlight`
- **Assert:** A sentence that is both a search match and the current playback highlight shows the playback highlight color (not the search color). When playback moves away, the search fill color remains.
- **Interactions:**
  1. Click Search, type `'Sentence'` — all sentences matched
  2. `playBtn().click()` — sentence 0 highlighted by playback
  3. `page.evaluate()` on `[data-index="0"]` — playback highlight uses `hexToRgba(highlightColor, 0.6)` (yellow), should NOT show search green/blue
  4. `driver.sendSentenceStart(1, SID.first)`, `driver.sendAudioChunk(MOCK_AUDIO_CHUNK)`, `page.clock.runFor(TICK.sentence)` — playback moves to sentence 1
  5. `page.evaluate()` on `[data-index="0"]` — now only search match color (green), playback color gone
  6. `page.evaluate()` on `[data-index="1"]` — playback highlight color (yellow, overrides search green)

---

## Change 4 — O(1) search diff (PDFViewer)

**File:** `frontend/tests/highlight-sync.spec.ts` — add new describe block `Search diff (O(1) optimization)`

**mock-data.ts changes:** None needed.

**WsDriver changes:** None needed.

The key observable behavior of `diffSets()`:
- Adding matches → only new match divs get `backgroundColor` set
- Removing matches → only removed match divs get `backgroundColor` cleared
- Unchanged matches are NOT touched (their `backgroundColor` stays as-is)

### Test 4.1: `adding_search_matches_only_styles_new_matches`
- **Assert:** When search query changes from narrow to broad, only newly added matches get styled; previously matched sentences keep their existing style without DOM mutation.
- **Interactions:**
  1. Click Search, type `'Sentence 0'` — only sentence 0 matched
  2. `page.evaluate()` — record `[data-index="0"]` element reference (`__testEl0`) and verify it has green/blue fill
  3. Clear input, type `'Sentence'` — now all 6 sentences matched
  4. `page.evaluate()` — verify `__testEl0` still has its fill (same object, not recreated)
  5. `page.evaluate()` — verify `[data-index="1"]` now has fill (newly added)
  6. Assert: sentence-0 div was NOT recreated (same `===` reference via `page.evaluate()` identity check)

### Test 4.2: `removing_search_matches_clears_only_removed`
- **Assert:** When search narrows, only removed matches lose their fill; remaining matches keep theirs.
- **Interactions:**
  1. Click Search, type `'Sentence'` — all 6 sentences matched
  2. `page.evaluate()` — `[data-index="0"]` has fill, `[data-index="1"]` has fill
  3. Clear input, type `'Sentence 0'` — only sentence 0 matched
  4. `page.evaluate()` — `[data-index="0"]` still has fill (still a match)
  5. `page.evaluate()` — `[data-index="1"]` fill is cleared (removed from matches)

### Test 4.3: `search_diff_preserves_current_highlight_on_narrow`
- **Assert:** When search narrows and the current match index changes, the previous current match reverts to non-current color and the new current match gets current color.
- **Interactions:**
  1. Click Search, type `'Sentence'` — 6 matches, current=0 (blue)
  2. Click next-match button 2 times → current=2 (index 2 is blue, 0 and 1 are green)
  3. Clear input, type `'Sentence 0'` — 1 match, current resets to 0
  4. `page.evaluate()` — `[data-index="0"]` has blue fill (current), `[data-index="2"]` has no search fill (removed from matches)

### Test 4.4: `diff_sets_with_empty_previous_stylles_all_new`
- **Assert:** First search (prev=empty, next=matches) styles all matches. Equivalent to test 3.1 but verifies the diff path from empty→nonempty.
- **Interactions:**
  1. Page loaded, no search open — `[data-index="0"]` has no search fill
  2. Click Search, type `'Sentence 0'` — 1 match
  3. `page.evaluate()` — `[data-index="0"]` has `backgroundColor` matching `SEARCH_CURRENT_COLOR`

### Test 4.5: `diff_sets_to_empty_clears_all`
- **Assert:** Clearing search (prev=matches, next=empty) clears all search fills.
- **Interactions:**
  1. Click Search, type `'Sentence'` — 6 matches, all styled
  2. Clear input → empty query
  3. `page.evaluate()` — all `[data-index]` divs have `backgroundColor === ''` (or just playback highlight if any)

---

## Summary of File Changes

### `frontend/tests/highlight-sync.spec.ts`

| Block | Action | Tests |
|---|---|---|
| `Word-level highlighting` | Extend (add 5 tests) | 1.3–1.7 |
| `Search highlight styling` | Unskip + rewrite + add 2 tests | 3.1–3.4 |
| `Search diff (O(1) optimization)` | New describe block | 4.1–4.5 |

### `frontend/tests/fixtures/mock-data.ts`
No changes needed — `words` arrays already present on sentences 0 and 1.

### `frontend/tests/fixtures/ws-driver.ts`
No changes needed — `sendSentenceEnd()` already supports `wordTimestamps` parameter.

### Helper constants to add at top of file

```ts
const SEARCH_INPUT = 'Search in book…'  // placeholder text for getByPlaceholder
const searchBtn = (page: Page) => page.getByRole('button', { name: 'Search' })
const searchInput = (page: Page) => page.getByPlaceholder(SEARCH_INPUT)
```
