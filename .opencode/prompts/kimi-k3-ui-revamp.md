# UI Revamp — Kokoro Ebook Reader

You are a senior product designer and Svelte engineer. Your job is to rebuild the **frontend UI** of this repo into a modern, sleek, fully responsive reading app, backed by a real design system and reusable components. Work directly in the codebase. Don't ask clarifying questions; when something is ambiguous, pick the option that keeps existing behavior intact and note it in your final summary.

Treat the codebase as the source of truth. Before you design anything, read every file listed under **Repo map**. The flow inventory below was taken from the code, but if the code disagrees with it, follow the code.

---

## 1. Stack and repo map

- **Frontend:** SvelteKit 2, Svelte 5 (runes only), TypeScript strict, Tailwind CSS v4 (`@import "tailwindcss"` in `src/app.css`, via `@tailwindcss/vite`), pdfjs-dist v5. Tests use Vitest and Playwright.
- **Backend:** FastAPI on `localhost:8000` with a WebSocket TTS stream. **Do not change the backend.**

```
frontend/src/
  app.css                         # currently just @import "tailwindcss"
  routes/+layout.svelte           # app shell: fixed 180px Sidebar + mobile hamburger drawer
  routes/+page.svelte             # "Text Reader": paste text → read aloud (idle | reading modes)
  routes/library/+page.svelte     # Library grid + "Resume Reading" card
  routes/reader/[id]/+page.svelte # Book reader (PDF → PDFViewer, EPUB/text → TextViewer)
  routes/voice/+page.svelte       # Voice picker, preview, upload .pt, delete custom
  routes/mp3/+page.svelte         # MP3 export list + "New Export" dialog, polls every 3s
  routes/upload/+page.svelte      # Opens UploadDialog on an empty page
  lib/api.ts                      # ALL HTTP + TTSSocket. Only place allowed to call fetch()
  lib/stores/audio.ts             # Web Audio playback engine (isPlaying, buffering, currentIndex, currentWordIndex, speed, voice)
  lib/stores/reader.ts            # bookId, sentences[], currentIndex, isPlaying, speed; loadBook/seek/setSpeed
  lib/stores/settings.ts          # localStorage settings: voice, highlightColor, highlightEnabled, autoscroll, hotkeysEnabled, bionic*
  lib/stores/user.ts              # last_book_id / last_sentence_index (server)
  lib/utils/hotkeys.ts            # global key map
  lib/utils/bionic-reading.ts
  lib/components/                 # AudioProgressBar, BookGrid, BookmarkPanel, LastRead, LibraryCard,
                                  # MediaBar, PDFViewer (585 lines), PageNavigator, SearchOverlay,
                                  # SettingsOverlay, Sidebar, TextViewer, TopToolbar, UploadDialog
```

---

## 2. Non-negotiable constraints

Svelte 5 rules are enforced by the compiler, so breaking them fails the build:

- Use `$props()`, `$state()`, `$derived()`, `$effect()`. Don't use `export let`, `$:`, or reactive `let`.
- Use `onclick` and other native event attributes, not `on:click`.
- Use callback props (`onSomething`), not `createEventDispatcher`.
- Use snippets and `{@render}`, not slots.
- **No `@apply`** anywhere, since this is Tailwind v4. Define tokens in `app.css` with `@theme` and CSS custom properties. Use `@custom-variant` for theme variants.
- **All network calls stay in `src/lib/api.ts`.** Components never call `fetch()`.
- **Don't change the audio engine's behavior.** You may read `audio.ts`, `reader.ts`, and `TTSSocket` state and call their public methods, but you must not change their internals: the generation counter, session IDs, decode chain, or rAF timing. They fix hard race conditions.
- **Keep PDFViewer's rendering pipeline.** PDF.js canvas pages, the absolutely positioned sentence/word overlay divs, and coordinate mapping (`x = fitz_x * scale`, no y-flip) must stay as they are. You may restyle the container, loading/error states, zoom control, and highlight colors. You may also extract the zoom control into a component.
- Browser `playbackRate` stays at 1.0. Speed goes through `audioStore.setSpeed`.
- Keep pdfjs imports at module level: `import * as pdfjsLib` and `workerUrl ?url`.
- Add **no new runtime dependencies** unless it's unavoidable, and justify each one. Don't add a component library: no daisyUI, shadcn-svelte, or Skeleton. Build your own primitives on Tailwind tokens. If you want icons, you may use `lucide-svelte` (ISC license). Otherwise, centralize the inline SVGs into a single `Icon.svelte` component.
- **Palette override:** this brief replaces the "slate-* neutrals, blue-500 primary" and "inline SVG icons" conventions in `.opencode/instructions.md`. Use the §8 tokens and `Icon.svelte` instead.
- **Don't copy any Readest source code.** Readest is AGPL-3.0 and written in React. Use it only as visual and interaction reference (see §5).

### Test contract: these selectors must keep working

Playwright specs in `frontend/tests/*.spec.ts` and Vitest specs in `frontend/src/tests/` depend on the following. Keep these accessible names, attributes, and roles, or update the tests in the same change and explain why.

- Buttons named `Play`, `Pause`, `Read Aloud`, `Retry`, and speed buttons `1x`, `1.5x`, `2x` inside `role="group"` named `Playback speed`
- `[aria-label="Settings"]` and `role="switch"` toggles in settings
- `#bionic-fixation`, `#bionic-ratio`
- `[data-sentence-index]`, `[data-highlighted="true"]`, `[data-word-index][data-sentence-index]`, `[data-index]`, `[data-overlay]`, `[data-bionic-overlay]`
- `button[data-loading="true"]` on the play button while buffering, and `.shimmer-shine` on the progress bar while buffering
- The texts `Resume Reading` and `Could not connect to the backend`, and book titles rendered as text (a card title may be an `h2`)
- `<canvas>` for PDF pages, and `<strong>` for bionic bold

Run the full test suite before and after your changes (`npm run check`, `npm run test:unit`, `npx playwright test`). The build must be clean with zero `svelte-check` errors.

---

## 3. Scope: design only the flows that actually exist

This is the complete inventory of real flows. Design **every state** of each one, and nothing beyond this list, except the small allowed enhancements in §3.3.

### 3.1 Flow inventory and required states

| # | Flow | Where | States you must design |
|---|---|---|---|
| F1 | **Paste text → Read aloud** | `/` | empty textarea (CTA disabled) · typing (character/word count, est. listen time) · submitting (button spinner, textarea locked) · error: empty / "No valid sentences found in text" (400) / backend down · reading mode (see F4) · **New text** confirmation if playback is active |
| F2 | **Save pasted text to library** | `/` reading mode | unsaved (Save action) · saving · saved (check + subtle success toast with "Open in Library" link) · save failed (inline + toast, retry) |
| F3 | **Upload PDF/EPUB** | `UploadDialog`, `/upload`, Library header | idle dropzone · drag-over (highlighted, scale/glow) · file selected (name, size, type chip, replace/remove) · invalid type (client-side `.pdf/.epub` check + server 400) · uploading (indeterminate progress; processing can be slow because of OCR/NLP, so show a "Processing… extracting sentences" sub-label) · success → navigate to reader · **already existed** (`already_existed: true` → toast "Already in your library", then open it) · network error + retry. **Drag-and-drop must actually work.** The current UI says "drag" but only click works. |
| F4 | **Reader: playback** | `/reader/[id]` and `/` reading mode | loading book (skeleton of the page/text) · book not found (404 → empty state + Back to Library) · sentences empty · ready/paused · buffering (`audio.buffering` → play button spinner with `data-loading`, shimmer progress, gentle pulse on current sentence) · playing · seeking (controls briefly disabled) · reached end · WebSocket unavailable. Controls: play/pause, back/forward (**5 sentences**, and the label must say so; today it wrongly says "15s"), speed (0.5–3x), elapsed/total estimate, click any sentence to seek. |
| F5 | **Reader: highlighting & autoscroll** | TextViewer / PDFViewer | sentence highlight (user colour, on/off) · word-level highlight as it's spoken · search match vs current match · bionic mode on/off (PDF: canvas dims, bionic overlay) · autoscroll on/off. See §7 for the full spec. |
| F6 | **Reader: page navigation (PDF)** | `PageNavigator` | current/total · prev/next disabled at the ends · jump-to-page input (validate and clamp, show an invalid state) · zoom in/out/reset (existing floating control) |
| F7 | **Reader: search in book** | `SearchOverlay` (F key) | closed · open, empty query · typing · N results with current/total and prev/next (Enter / Shift+Enter) · **no results** state · close (Esc) clears highlights |
| F8 | **Reader: bookmarks** | `BookmarkPanel` (B key adds) | loading · empty · list (page, label, date) · add (optimistic insert + toast "Bookmarked") · add failed (**today this fails silently, so surface it**) · delete (with undo toast) · delete failed · go to bookmark (closes the panel and seeks) |
| F9 | **Reader settings** | `SettingsOverlay` | highlight colour swatches · sentence highlight switch · autoscroll · hotkeys · bionic mode, with fixation and strength sliders shown only when on · hotkey reference. Settings apply live, with a visible preview. |
| F10 | **Library** | `/library` | loading (card skeletons, not a spinner line) · backend down (message containing "Could not connect to the backend", Retry) · empty (illustration + Upload CTA + "or paste text" link) · populated grid · Resume Reading card (only if `last_book_id` is set and the book exists; show **progress**, not `page_count`, which the current UI mislabels as "Page") · card menu → Delete → **custom confirm dialog** (replace `window.confirm`) → deleting → removed with an exit animation · delete failed |
| F11 | **Voices** | `/voice` | loading skeletons · backend down + Retry · grid grouped or filterable by accent (en-US/en-GB) and gender · selected voice (persists to settings) · preview idle/loading/playing/stop · preview failed (503/400, toast) · upload `.pt` (picker, uploading, success toast, 400 "Only .pt…" error) · delete custom voice (confirm, 403 for built-in voices, so hide the action for them) |
| F12 | **MP3 export** | `/mp3` | loading · backend down · empty (CTA) · list items: pending / processing (real progress bar with %) / done (size + Download) / error (show `error_message` with an expand option, not only a tooltip) · New Export sheet: book select (empty-library state inside the sheet) → voice → speed → submitting → success toast · delete export (confirm) · polling continues silently |
| F13 | **Global shell & navigation** | `+layout.svelte`, `Sidebar` | desktop sidebar · tablet rail · mobile bottom tab bar · active route · route transitions · immersive reader mode (see §6) |
| F14 | **Keyboard** | `hotkeys.ts` | Space, ←/→, ↑/↓, B, F, Esc. Add a `?` shortcut sheet only if it reuses the existing hotkey reference content. Keep visible focus rings on everything. |

### 3.2 Remove or ignore dead UI (don't design for it)

- **Account**: `Sidebar` links to `/account`, and that route doesn't exist. Remove the link.
- **CC toggle** and **Copy text** in `TopToolbar` are wired to `() => {}`. Remove them from the UI (you may keep the props optional).
- Unused API surface, such as `getExportStatus`, `userStore.clearLastRead`, `/documents/text/cleanup`, and the backend OCR internals. Don't build UI for these.
- Don't invent accounts/auth, cloud sync, annotations/notes, dictionary lookup, translation, OPDS, split-screen, or a stats dashboard. Readest has these, but this repo doesn't.

### 3.3 Allowed frontend-only enhancements

These are the only additions permitted. Each one needs no backend changes, and each must be small and fully stateful.

1. **Theme**: Light / Sepia / Dark (plus "System"), stored in `settingsStore` (localStorage) and applied via `data-theme` on `<html>`, with no flash of the wrong theme on load. The reader surface, PDF page background, and highlight colours must all stay legible in every theme. Highlight colours should use theme-aware alpha.
2. **Reader text size and line height** for TextViewer only (not PDF), stored in `settingsStore`.
3. **Chapter list / TOC for EPUB**. `Sentence.chapter` and `chapter_title` already come back from the API. Derive chapters client-side, jump by seeking to the first sentence of a chapter, and show the current chapter in the reader header. If a book has 0 or 1 chapters, hide the TOC entirely.
4. **"Back to current sentence" pill**. When autoscroll is on and the user scrolls away manually, pause autoscroll following and show a floating pill. Tapping it re-centres the current sentence and resumes following.
5. **Toasts and a confirm dialog** as shared primitives, replacing `window.confirm` and silent failures.
6. **Human-readable errors**. Add an `ApiError` class in `api.ts` with `status` and a `detail` parsed from FastAPI's `{ "detail": "…" }` body. Map it to friendly copy in one place (`lib/utils/errors.ts`). Components never show raw `HTTP 500: Internal Server Error`.

---

## 4. Architecture: abstraction and reuse

Restructure as follows. Move files where it improves clarity, and update imports.

```
src/lib/
  ui/                     # design-system primitives, no app knowledge, no stores, no api
    Button.svelte         # variants: primary | secondary | ghost | danger | icon; sizes sm|md|lg; loading; disabled; href
    IconButton.svelte     # 44×44 min hit area on coarse pointers, tooltip on fine pointers, required aria-label
    Icon.svelte           # single source for icons
    Switch.svelte  Slider.svelte  SegmentedControl.svelte  Select.svelte  TextField.svelte  TextArea.svelte
    Card.svelte  Badge.svelte  Chip.svelte  ProgressBar.svelte (determinate | indeterminate | shimmer)
    Spinner.svelte  Skeleton.svelte  Kbd.svelte  Tooltip.svelte  Menu.svelte (popover menu, keyboard nav)
    Dialog.svelte         # centred on ≥md, bottom sheet on <md, same API
    Sheet.svelte          # side panel on ≥lg (docked or overlay), bottom sheet with drag handle on <md
    ConfirmDialog.svelte
    Toaster.svelte        # renders the toast store
    EmptyState.svelte     # icon, title, body, primary/secondary action snippets
    ErrorState.svelte     # message + Retry; tone: inline | panel | page
    AsyncView.svelte      # snippets: loading / error / empty / content. The ONE way to render async data
    Dropzone.svelte
  stores/
    toast.ts              # push({ tone: success|error|info, title, action? }), auto-dismiss, max 3
    ui.ts                 # layout state: sidebar collapsed, panel open, immersive mode
  components/
    shell/  AppShell, SideNav, NavRail, BottomTabBar
    reader/ ReaderShell, ReaderHeader, PlayerDock, TransportControls, SpeedControl,
            ProgressScrubber, TextViewer, PDFViewer, ZoomControl, PageNavigator,
            SearchBar, BookmarksPanel, SettingsPanel, ChapterList, BackToCurrentPill
    library/ BookCard, BookGrid, ResumeCard, UploadButton
    voices/  VoiceCard, VoiceFilters
    exports/ ExportRow, NewExportSheet
```

Rules:

- **One `ReaderShell`** serves both `/` reading mode and `/reader/[id]`. Today those two pages duplicate the header, MediaBar, progress bar, and handlers. The shell takes snippets for header actions and the viewer body, plus callbacks. The route files should be thin, around 150 lines or less.
- Every async surface goes through `AsyncView`, so loading, error, and empty states look the same everywhere.
- Primitives take a `class` prop and spread `...rest` onto the root. Don't hard-code widths; the parent controls layout.
- Keep variant styling in small typed maps (`const variants = { primary: '…', … } as const`) instead of long ternaries in markup.
- Don't put `<style>` blocks in components, except `:global()` overrides where Tailwind can't reach. Keyframes go in `app.css`.

---

## 5. Inspiration: combine Natural Reader and Readest

Research both before designing. If you can browse, look at the Readest repo and web app (`github.com/readest/readest`, `web.readest.com`) and NaturalReader's web app (`naturalreaders.com/online`). If you can't browse, work from the notes below.

**Take from NaturalReader (listening-first):**
- The player is the hero: a large, always-reachable play button, visible speed and voice, and a clear sentence highlight that moves with the audio.
- Voice selection is one tap from the reader. Show the current voice's name in the player dock, and tap it to open a compact voice picker sheet that reuses `VoiceCard` from F11. It's the same data, so this isn't a new flow.
- A simple "paste text and listen" landing with a big, friendly text area and one obvious CTA.

**Take from Readest (reading-first, calm, modern):**
- **Immersive reader**: the app nav disappears in the reader. The header and footer bars are thin, translucent (`backdrop-blur`), and **auto-hide** while reading. They reappear on tap in the centre of the page (touch), on mouse movement near the edge (desktop), or on any keyboard focus.
- Side panels (TOC, bookmarks, search results) slide in from the edge. They're docked on wide screens and overlay on narrow ones.
- The settings panel is organised in tabs or sections: **Appearance** (theme, text size, line height), **Highlight** (colour, on/off, bionic), **Playback & Keys** (autoscroll, hotkeys, reference).
- A library shelf of cover-style cards with a subtle hover lift, a file-type badge, and a thin progress bar at the bottom of the card when progress is known.
- A neutral, low-chroma palette with one accent, generous whitespace, and a well-set reading column (60–75ch).

**Result:** Readest's calm, immersive reading canvas combined with NaturalReader's prominent, always-present listening dock.

---

## 6. Layouts and responsiveness (mobile, iPad, desktop)

Use Tailwind's default breakpoints, and test at each width listed: **375, 430 (phones), 768/834 (iPad portrait), 1024/1180/1366 (iPad landscape, iPad Pro), 1440+ (desktop)**. Design for both touch and pointer: use `pointer-coarse:` / `pointer-fine:` custom variants (via `@custom-variant`) for hit sizes and hover-only affordances. Never hide an essential action behind hover.

| | Phone `< md` | iPad portrait `md–lg` | iPad landscape / desktop `≥ lg` |
|---|---|---|---|
| App nav | Bottom tab bar: Read · Library · Voices · Exports. Upload is a button in Library and on the Read page. Respect `env(safe-area-inset-bottom)`. | Icon rail (72px) with labels on long-press/tooltip | Full sidebar (~240px), collapsible to a rail; collapsed state persisted |
| Reader chrome | Top bar: back, title (truncated), overflow menu (search, bookmarks, TOC, settings). Bottom **PlayerDock** with play/pause as the largest target. Speed opens as a segmented sheet. | Top bar shows search/bookmarks/settings inline. Dock is a floating rounded bar, centred, max-w-xl | Same, with **docked** side panel (TOC/bookmarks/search) that pushes content instead of covering it; dock centred under the reading column |
| Panels & dialogs | Bottom sheets with a drag handle, swipe down to dismiss, max 85dvh | Sheets from the right, overlay | Docked right panel (~360px); dialogs centred |
| Library grid | 2 cols | 3–4 cols | 5–6 cols, max content width ~1280px |
| Reading column | Full width, 20px gutters | max ~68ch, centred | max ~72ch; PDF keeps its existing fit-to-width logic (max 900px) |

Other requirements:

- Use `100dvh`, not `h-screen`, so iOS Safari's toolbars don't cut off the dock.
- Keep the viewport meta tag, and add `viewport-fit=cover`.
- The current mobile hamburger button overlaps page content. Replace it with the bottom tab bar.
- Handle orientation changes without losing scroll position or the current sentence.
- No horizontal scroll at any width. Long titles truncate with `title` attributes.

---

## 7. Highlighting, scrolling, and reading surface spec

- **Sentence highlight**: a soft rounded background in the user's colour, at theme-aware opacity (about 0.45 light, 0.30 dark). Colour changes animate over 180ms. The previous sentence fades out while the next fades in; no hard jumps. Keep `data-highlighted`, `data-sentence-index`, and `aria-current` exactly as they are.
- **Word highlight** (TextViewer): the active word gets a stronger tint plus a slight weight change *without reflow*. Use a background or box-shadow underline instead of `font-weight` if weight shifts the layout. Words already spoken stay slightly tinted. PDFViewer word divs keep `data-word-index` and `data-sentence-index` and get the same visual language.
- **Search**: all matches get a subtle outline or underline tint; the current match gets the accent colour. They must stay distinguishable from the playback highlight in every theme and colour choice. The colour constants in PDFViewer (`SEARCH_*_COLOR`) should come from CSS variables.
- **Buffering**: a gentle opacity breathe on the current sentence (not Tailwind's harsh `animate-pulse`), the shimmer on the progress bar, and the spinner in the play button. Buffering must never show when the user has paused.
- **Autoscroll**: use smooth `scrollIntoView({ block: 'center' })`, but only if the target sentence is outside the middle 60% of the viewport, so the page doesn't jitter on every sentence. Detect user scroll (wheel/touch/keyboard) and suspend following, then show the Back to Current pill (§3.3.4). Under `prefers-reduced-motion`, use `behavior: 'auto'`.
- **Sentence hover and tap**: on fine pointers, hover shows a faint tint and a pointer cursor ("click to play from here"). On touch, a tap shows a brief pressed state.
- **Reading typography** (TextViewer): a serif reading face for body text (for example `Literata`, `Source Serif 4`, or a system serif stack; self-host or use the system font, and don't block rendering), line-height 1.7, paragraph spacing, and `text-wrap: pretty`. The UI chrome uses a sans face (Inter or a system stack).

---

## 8. Design system (tokens)

Put all tokens in `src/app.css` using Tailwind v4 `@theme`, with CSS variables overridden per `[data-theme]`. Components use semantic utilities only (`bg-surface`, `text-fg-muted`, `border-border`, `bg-accent`), never raw `slate-*` or `blue-*`.

### 8.1 Colour palette: "Ink & Iris"

The palette uses warm paper neutrals (bookish, easy on the eyes over long sessions) and one confident **Iris** violet-indigo accent for everything interactive or "listening". The accent is deliberately far from the yellow default highlight, so the playback highlight and UI chrome never compete. Use these exact values as the starting point. You may nudge a value to pass contrast, but keep the character.

**Semantic tokens × themes**

| Token | Light | Sepia | Dark | Use |
|---|---|---|---|---|
| `bg` | `#F7F6F3` | `#F1E7D4` | `#111113` | app background |
| `surface` | `#FFFFFF` | `#F8F0E0` | `#19191C` | cards, bars, panels |
| `surface-raised` | `#FFFFFF` | `#FBF5EA` | `#222226` | menus, dock, dialogs |
| `surface-sunken` | `#EFEDE8` | `#E8DCC4` | `#0B0B0D` | inputs, wells, PDF backdrop |
| `reader-page` | `#FFFFFF` | `#F8F0E0` | `#1A1A1D` | reading column / page paper (never pure black) |
| `border` | `#E4E1DA` | `#DCCDB0` | `#2C2C31` | hairlines |
| `border-strong` | `#CFCBC2` | `#C7B391` | `#3E3E45` | input borders, dividers |
| `fg` | `#1C1B19` | `#3B2F22` | `#EDEDF0` | primary text |
| `fg-muted` | `#5E5A53` | `#6B5A45` | `#A8A8B3` | secondary text |
| `fg-subtle` | `#8C877E` | `#927E63` | `#72727D` | captions, placeholders (not for body text) |
| `accent` | `#5146D9` | `#5B47C9` | `#8B85FF` | primary buttons, active nav, progress, play button |
| `accent-hover` | `#4338CA` | `#4B39B3` | `#A09BFF` | hover/pressed |
| `accent-fg` | `#FFFFFF` | `#FFFFFF` | `#0F0E1A` | text/icons on accent |
| `accent-soft` | `#ECEBFC` | `#E6DDF0` | `#25234A` | selected cards, active tab bg, chips |
| `success` / `-soft` | `#1F8A5B` / `#E3F4EC` | `#2F7A4F` / `#E1ECD6` | `#3DD68C` / `#10291E` | saved, export done |
| `warning` / `-soft` | `#B7791F` / `#FBF1DE` | `#A0661A` / `#F3E2C2` | `#F5B94A` / `#2E2310` | processing, already-existed notices |
| `danger` / `-soft` | `#D1453B` / `#FBE8E6` | `#B53A2F` / `#F3D9D0` | `#FF6B61` / `#331614` | errors, delete |
| `focus-ring` | `#7C74F0` | `#7C63D9` | `#A09BFF` | focus-visible outline |
| `overlay` | `rgb(20 18 15 / .45)` | `rgb(59 47 34 / .40)` | `rgb(0 0 0 / .60)` | scrim behind sheets/dialogs |
| `highlight-alpha` | `.45` | `.50` | `.30` | multiplier for sentence highlight colour |
| `word-alpha` | `.85` | `.85` | `.60` | active word highlight |

**Highlight swatches.** These replace the colour list in `SettingsOverlay`. Store them as hex and render them with `color-mix(in oklab, <hex> calc(var(--highlight-alpha)*100%), transparent)`, so a single user choice looks right in every theme.

| Name | Hex | | Name | Hex |
|---|---|---|---|---|
| Honey *(default)* | `#FCD34D` | | Lavender | `#C4B5FD` |
| Mint | `#6EE7B7` | | Rose | `#F9A8D4` |
| Sky | `#7DD3FC` | | Coral | `#FCA5A5` |
| Peach | `#FDBA74` | | Aqua | `#67E8F9` |

Legacy stored values (`#fef08a`, `#86efac`, `#93c5fd`, …) must still load, since `isValidColor` accepts any `#RRGGBB`. Also update `VALID_COLORS` and `DEFAULTS.highlightColor` in `settings.ts` to the new swatches.

**Search colours** come from the accent, not hard-coded RGBA:
- Match: `accent` at 16% fill plus a 1px `accent` outline at 40%.
- Current match: `accent` at 32% fill plus a 2px solid `accent` outline.

**Brand.** Rename the "EbookReader" wordmark to **Kokoro Reader**, set in `font-ui` semibold, with a small Iris sound-wave or open-book glyph. Update `favicon.svg` to match (an Iris mark on a transparent background).

**Contrast.** `fg` and `fg-muted` must pass AA (4.5:1) on `bg`, `surface`, and `reader-page` in every theme, and `accent-fg` must pass AA on `accent`. Check every theme and document the ratios in `DESIGN.md`.

**Wiring in Tailwind v4.** Register the tokens in `@theme` so utilities like `bg-surface` and `text-fg-muted` exist, and swap the values per theme:

```css
@import "tailwindcss";
@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));
@custom-variant sepia (&:where([data-theme="sepia"], [data-theme="sepia"] *));
@custom-variant pointer-coarse (@media (pointer: coarse));
@custom-variant pointer-fine (@media (pointer: fine));

@theme {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-fg: var(--fg);
  --color-accent: var(--accent);
  /* …every token in the table… */
}
:root, [data-theme="light"] { --bg: #F7F6F3; --surface: #FFFFFF; --fg: #1C1B19; --accent: #5146D9; /* … */ }
[data-theme="sepia"]        { --bg: #F1E7D4; --surface: #F8F0E0; --fg: #3B2F22; --accent: #5B47C9; /* … */ }
[data-theme="dark"]         { --bg: #111113; --surface: #19191C; --fg: #EDEDF0; --accent: #8B85FF; /* … */ color-scheme: dark; }
```

"System" resolves via `matchMedia('(prefers-color-scheme: dark)')` to light or dark (never sepia) and follows live changes.

### 8.2 Other tokens
- **Typography:** `font-ui`, `font-reading`, `font-mono`. Type scale: `xs` 12, `sm` 14, `base` 16, `lg` 18, `xl` 20, `2xl` 24, `3xl` 30, `display` 36–48 (for the Read page title), each with line-heights. Use tabular numerals for all times and counters.
- **Spacing:** Tailwind's 4px scale. Page gutters are 16 / 24 / 32 by breakpoint.
- **Radius:** `sm` 6, `md` 10, `lg` 14, `xl` 20, `full`. Cards use `lg`, the dock uses `xl`, sheets use `xl` on top corners.
- **Elevation:** `shadow-1` (cards), `shadow-2` (menus, dock), `shadow-3` (dialogs, sheets). Keep them soft and low-opacity, and tint them in dark mode, where borders do more of the work.
- **Motion:**
  - Durations: `--dur-fast: 120ms`, `--dur-base: 200ms`, `--dur-slow: 320ms`.
  - Easing: `--ease-out: cubic-bezier(.2,.8,.2,1)`, `--ease-in-out: cubic-bezier(.4,0,.2,1)`, and a spring-like `--ease-emph: cubic-bezier(.3,1.4,.5,1)` used sparingly.
  - Wrap everything in `motion-safe:` or zero it out under `prefers-reduced-motion`.
- **Z-index layers:** `base`, `sticky`, `dock`, `panel`, `overlay`, `dialog`, `toast`, `tooltip`, named in one place.
- **Focus:** a 2px `focus-ring` outline with offset on every interactive element, via `focus-visible` only.

Deliver a **`/design` route** (dev-only: hide it in production builds or leave it out of the nav) that renders every primitive in every variant and state, in all three themes. It works as both a living style guide and a visual regression target.

---

## 9. Motion and transitions

- **Route changes:** use SvelteKit `onNavigate` with `document.startViewTransition` when it's available: a cross-fade plus 8px slide. Fall back to an instant change. The library card cover morphs into the reader header (`view-transition-name` per book id) where supported.
- **Lists:** Svelte `animate:flip` plus `fly`/`fade` for grid and list insertions and removals (books, bookmarks, exports, toasts).
- **Sheets and dialogs:** scale 0.98→1 with a fade (dialogs), or slide from the edge (sheets). The scrim fades. Close with Esc, a scrim click, or a swipe-down on mobile sheets. Trap focus and restore it on close.
- **Chrome auto-hide:** translate plus fade over `--dur-slow`. It never hides while a panel, menu, or input has focus.
- **Micro-interactions:** the play↔pause icon morphs (scale/rotate cross-fade), buttons get pressed scale 0.97, switches spring, the progress bar width transitions linearly, and skeletons shimmer.
- Animate only transform and opacity. Keep interactions at 60fps; no layout thrash on the highlight path, which updates every word.

---

## 10. Accessibility

- Everything works by keyboard alone, with a logical tab order and a skip-to-content link.
- Dialogs and sheets use `role="dialog"`, `aria-modal`, a labelled title, a focus trap, and focus restore.
- Toasts use an `aria-live="polite"` region, with errors `assertive`.
- The current sentence is announced only on user seek, not on every auto-advance.
- Icon-only buttons always have `aria-label`. Hit targets are at least 44×44 on coarse pointers.
- Colour is never the only signal: search matches also get an outline, and error states get an icon.

---

## 11. Execution plan

Work in phases. After each phase, run `npm run check && npm run test:unit` and fix everything before moving on. Run Playwright at the end of phases 3, 4, and 8.

0. **Audit:** read all files, then write `frontend/DESIGN.md` covering the token table, the component inventory, and the state matrix from §3.1 with each state mapped to its component.
1. **Tokens and theming:** `app.css` `@theme`, the three themes, no-flash theme init script in `app.html`, fonts.
2. **Primitives:** everything in `lib/ui/`, plus `toast.ts`, `ApiError` and `errors.ts`, and the `/design` route.
3. **Shell:** AppShell with sidebar, rail, and bottom tabs; view transitions; remove dead links.
4. **Reader:** ReaderShell, PlayerDock, header auto-hide, panels (bookmarks, search, TOC, settings), highlight and autoscroll spec, Back-to-Current pill. Migrate both `/` reading mode and `/reader/[id]`.
5. **Read (paste) page:** F1 and F2.
6. **Library and Upload:** F3 and F10, including real drag-and-drop and confirm-delete.
7. **Voices and Exports:** F11 and F12, including the in-reader voice picker sheet.
8. **Polish:** reduced motion, dark/sepia contrast pass, all breakpoints in §6, a keyboard-only pass, and removal of unused components and CSS.

---

## 12. Acceptance checklist (verify each item before finishing)

- [ ] `npm run check` shows 0 errors. `npm run test:unit` and `npx playwright test` pass, or any test changes are listed and justified.
- [ ] No `export let`, `$:`, `on:click`, `createEventDispatcher`, `@apply`, or `fetch(` outside `api.ts` (verify with grep).
- [ ] No raw palette classes (`slate-`, `blue-`, `red-`…) in components; tokens only. The Ink & Iris palette (§8.1) is implemented for all three themes, and contrast ratios are recorded in `DESIGN.md`.
- [ ] Every flow F1–F14 renders every state listed in §3.1. The `/design` page shows each primitive in every state.
- [ ] No UI for Account, CC, Copy text, or anything else in §3.2.
- [ ] The labels "15s" → "5 sentences", the Resume card's "Page N", and the bookmark page number (display 1-based) are all corrected.
- [ ] No silent failures: every caught error either shows inline or raises a toast.
- [ ] Verified at 375, 768, 1024, and 1440 in light, sepia, and dark, and with `prefers-reduced-motion`.
- [ ] Playback, seek, speed change, and word highlighting behave exactly as before. The audio engine is untouched.

## 13. Final output

When you're done, reply with:

1. A summary of the new structure (file tree of `lib/ui` and `lib/components`).
2. The state matrix: flow → states → component.
3. Any deviations from this brief and why.
4. Anything you couldn't verify.
