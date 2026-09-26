# Kokoro Ebook Reader — Improvement Plan

> Triple-pass reviewed plan based on research of pdf-narrator, readest, pdf-translator, foliate-js, Kokoro, and misaki source code.

## Research Corrections & Key Findings

1. **Contraction splitting is NOT a bug.** Misaki's `retokenize()` + `merge_tokens()` merges spaCy sub-word tokens back into complete words before Kokoro sees them. `"don't"` → spaCy `["do","n't"]` → misaki merges → 1 MToken with `text="don't"`, 1 timestamp. Matches `text.split(/\s+/)` perfectly.

2. **Punctuation tokens cause minor visual offset.** SpaCy splits `"Hello, world!"` into 4 tokens with timestamps, but `text.split(/\s+/)` gives 2 words. Punctuation timestamps have no bbox overlay (invisible, harmless), but word highlight index gets slightly offset. P3 cosmetic issue.

3. **Kokoro's internal pipeline already handles abbreviation/number expansion.** Misaki expands `"Dr."` → `"Doctor"` (phonemes), `"42"` → `"forty two"` (phonemes), etc. But `MToken.text` stays as original — Kokoro only expands for phonemization. Our stored text and Kokoro's token text stay aligned. Phase 2 text cleaning (abbreviation expansion, num2words) is **redundant** and should be dropped.

4. **PyMuPDF `get_toc()` returns 1-based page numbers.** Our `pdf_engine.py` uses 0-based. Must convert with `page - 1`.

5. **Kokoro's own `__main__.py` uses `* 32767`, not `* 32768`.** Our code uses `* 32768.0` with `.clip()`.

---

## P1 — High-Value, Low-Risk

### 1.1 Text Cleaning — Unicode Normalization Only

Port from pdf-narrator's `normalize_text()` + readest's `preprocessSSML()`:

**What to clean (preserves word count, zero sync risk):**
- Unicode NFKC normalization
- Smart quotes → straight quotes (`'` `"`)
- Em-dash → `", "`, en-dash → `", "` (TTS pause cue)
- Guillemets → standard quotes
- Zero-width characters stripped
- Ellipsis → `"."`
- Excess whitespace collapse

**What NOT to clean (redundant with Kokoro/misaki internal pipeline):**
- ~~Abbreviation expansion~~ — misaki handles `"Dr."` → `"Doctor"` during phonemization
- ~~Number-to-words~~ — misaki handles `"42"` → `"forty two"` during phonemization
- ~~Sentence pause injection~~ — Kokoro handles pauses via punctuation phonemes

**Implementation:**
- Create `backend/services/text_cleaner.py` with `normalize_text(text: str) -> str`
- Apply in `documents.py` to ALL three ingestion paths (PDF, EPUB, text) on `s.text` before creating `Sentence`
- No changes to `tts_engine.py` or frontend

**Files:** `services/text_cleaner.py` (new), `documents.py:47-67`

### 1.2 PDF Zoom

**Architecture:** Two-layer scale: `fitScale` (container-responsive) × `zoomLevel` (user control).

**Frontend `PDFViewer.svelte`:**
- Add `let zoomLevel = $state(1.0)` — range [0.5, 3.0], step 0.15
- Compute `let finalScale = $derived(effectiveScale * zoomLevel)`
- Use `finalScale` for `page.getViewport({ scale: finalScale })` and all overlay positions
- Debounce re-render (150ms), only render visible pages + 1 neighbor
- No backend changes needed

**Frontend controls (MediaBar or new component):**
- Zoom in/out buttons with current zoom percentage display
- Keyboard shortcuts: Ctrl+Plus, Ctrl+Minus, Ctrl+0 (reset)

**Files:** `PDFViewer.svelte`, `MediaBar.svelte` or new `ZoomControls.svelte`

### 1.3 Chapter Detection — Backend

**Schema changes:**
- Add `chapter: int = 0` and `chapter_title: Optional[str] = None` to `base_engine.py:SentenceRecord`
- Add same fields to `db/models.py:Sentence` with defaults
- Add migration in `database.py:_migrate()`:
  ```sql
  ALTER TABLE sentence ADD COLUMN chapter INTEGER DEFAULT 0;
  ALTER TABLE sentence ADD COLUMN chapter_title TEXT DEFAULT NULL;
  ```

**PDF chapter detection in `pdf_engine.py`:**
- Call `doc.get_toc()` — returns `[[level, title, page_1based], ...]`
- Convert to 0-based: `chapter_page = toc_item[2] - 1`
- Build chapter boundaries: `[(title, start_page_0based), ...]`
- Map each sentence to chapter by page range: `sentence.page >= chapter_start and sentence.page < next_chapter_start`
- If TOC is empty/None, fall back to font-size heuristic (largest font = heading) — port from pdf-narrator's `split_text_into_heuristic_chapters()`
- Integrate with existing `TextFilter`: when `should_filter()` returns `FilterReason.CHAPTER_HEADING`, extract the title and tag subsequent sentences

**EPUB chapter detection in `epub_engine.py`:**
- Each `ebooklib.ITEM_DOCUMENT` is a chapter — use item name as chapter title
- Increment `chapter` counter per item
- Trivially free, no heuristic needed

**API changes:**
- `documents.py:get_sentences()` adds `chapter` and `chapter_title` to response
- `api.ts:Sentence` adds `chapter: number` and `chapter_title?: string`

**Files:** `base_engine.py`, `models.py`, `database.py`, `pdf_engine.py`, `epub_engine.py`, `documents.py`, `api.ts`

---

## P2 — High-Value, Moderate Effort

### 2.1 Chapter Navigation UI

Depends on P1.3 (chapter data in sentences).

- Extract chapters from sentences: scan for `chapter_title != null` to build `[{index, title, sentenceCount}]`
- Dropdown or sidebar: click chapter → `seek(firstSentenceIndex)`
- Highlight current chapter during playback
- Show per-chapter progress (sentences played / total)

**Files:** New `ChapterNav.svelte`, reader layout integration

### 2.2 PDF Line-Join Fix

PDFs with hard line breaks mid-sentence produce fragments like `"The quick brown"` / `"fox jumps over"` as separate sentences.

- In `pdf_engine.py`, detect and merge blocks that are wrapped lines (same x-offset, consecutive y, same font size)
- Port core logic from pdf-narrator's `join_wrapped_lines()`
- Runs BEFORE spaCy segmentation

**Files:** `pdf_engine.py:49-55`

### 2.3 Punctuation Token Alignment Fix

SpaCy splits `"Hello, world!"` into `["Hello", ",", "world", "!"]` — 4 tokens with timestamps. But `text.split(/\s+/)` gives `["Hello,", "world!"]` — 2 words. The extra punctuation timestamps cause word highlight index offset.

**Fix:** In `tts_engine.py`, after collecting `word_timestamps`, filter out entries where `t.text` is purely punctuation (no letters or digits). This aligns `len(word_timestamps)` with `len(text.split(/\s+/))`.

**Files:** `tts_engine.py:114-121`

---

## P3 — Medium-Value Polish

### 3.1 Per-Chapter TTS Pause

In `tts.py:_consumer_with_events()`, when `sentence_data[idx]["chapter"] != sentence_data[idx+1]["chapter"]`, inject 500ms silence between sentences. Requires chapter field from P1.3.

**Files:** `tts.py:64-116`

### 3.2 Audio Normalization Fix

Change `(full_audio * 32768.0)` → `(full_audio * 32767.0)` in `tts_engine.py:139` and `:215`. Matches Kokoro's own `__main__.py`. The `.clip()` can be removed since `* 32767` can't overflow int16.

Accept minor volume difference with existing cached audio (inaudible ~0.003% difference).

**Files:** `tts_engine.py:139`, `tts_engine.py:215`

### 3.3 Multiple Highlight Styles

CSS-driven styles: fill (current default), underline, outline. Configurable per user preference. Low effort, purely frontend CSS changes on overlay divs.

**Files:** `PDFViewer.svelte`, `TextViewer.svelte`

---

## P4 — Nice-to-Have, High Effort

### 4.1 Scanned PDF OCR Fallback

- Wire up existing `pdf_engine.py:is_image_page()` (currently defined but unused)
- Add `services/ocr.py` using easyOCR
- Heavy new dependency (torch + easyOCR)

### 4.2 `_proportional_timestamps` G2P Divergence

Currently uses misaki G2P standalone, which may tokenize differently than Kokoro's pipeline. Fix: deprecate `_proportional_timestamps` to pure proportional-by-character fallback (remove G2P dependency). Kokoro's own timestamps should always be preferred.

**Files:** `tts_engine.py:33-54`

### 4.3 TOC Chapter Overlap Detection

Some PDFs have overlapping TOC entries (same page, multiple chapters). Add detection + first-wins strategy.

---

## Dependency Graph

```
P1.1 Text Cleaning          (standalone)
P1.2 PDF Zoom               (standalone, frontend only)
P1.3 Chapter Detection      (standalone, backend + schema)
  ↓
P2.1 Chapter Nav UI         (depends on P1.3)
P2.2 PDF Line-Join          (standalone)
P2.3 Punctuation Fix        (standalone)
  ↓
P3.1 Per-Chapter Pause      (depends on P1.3)
P3.2 Audio Normalization    (standalone)
P3.3 Highlight Styles       (standalone)
  ↓
P4.x                         (all standalone)
```

## Suggested Implementation Order

1. **P1.1** Text Cleaning — simplest, least risky, immediate TTS quality improvement
2. **P1.2** PDF Zoom — purely frontend, zero backend risk
3. **P1.3** Chapter Detection Backend — schema migration + extraction logic
4. **P2.1** Chapter Nav UI — depends on P1.3
5. **P2.3** Punctuation Fix — small targeted fix
6. **P2.2** PDF Line-Join — moderate complexity in pdf_engine
7. **P3.x** Polish items in any order
8. **P4.x** As needed

## Source Repos Researched

| Repo | What We Took | What We Rejected |
|------|-------------|-----------------|
| **pdf-narrator** | `normalize_text()`, TOC-based chapter detection, font-size heuristic fallback, `join_wrapped_lines()` concept | `expand_abbreviations()` (redundant with Kokoro), `convert_numbers()` (redundant), `handle_sentence_ends_and_pauses()` (redundant) |
| **readest** | SSML regex patterns (em-dash, zero-width, ellipsis), per-chapter pause concept | SSML `<mark>` highlighting (wrong paradigm for Kokoro), `pdf.getOutline()` only (we add heuristic fallback) |
| **pdf-translator** | Validation of bbox-based overlay approach | Pixel-compositing (overkill for our use case) |
| **foliate-js** | None directly applicable | `Intl.Segmenter` (nice but not needed yet), SSML mark paradigm |
| **Kokoro/misaki source** | Token alignment understanding, `*32767` normalization, confirmation that internal pipeline handles abbreviations/numbers | — |
