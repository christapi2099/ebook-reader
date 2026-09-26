# DESIGN.md — Kokoro Reader design system

Source of truth for the UI revamp. Implements the brief in
`.opencode/prompts/kimi-k3-ui-revamp.md` ("Ink & Iris" palette, three themes,
Svelte 5 + Tailwind v4). Components use semantic utilities only
(`bg-surface`, `text-fg-muted`, `border-border`, `bg-accent`) — never raw
palette classes (`slate-*`, `blue-*`, `red-*`…).

## 1. Tokens

All tokens live in `src/app.css`: registered in `@theme` as Tailwind v4 theme
keys that reference CSS custom properties, with the property values swapped
per `[data-theme]` on `<html>`. Themes: `light` (default, also `:root`),
`sepia`, `dark`. "System" resolves via `matchMedia('(prefers-color-scheme:
dark)')` to light or dark (never sepia) and follows live changes. Theme is
applied by a no-flash inline script in `app.html` before first paint.

### 1.1 Colour tokens × themes

| Token | Light | Sepia | Dark | Use |
|---|---|---|---|---|
| `bg` | `#F7F6F3` | `#F1E7D4` | `#111113` | app background |
| `surface` | `#FFFFFF` | `#F8F0E0` | `#19191C` | cards, bars, panels |
| `surface-raised` | `#FFFFFF` | `#FBF5EA` | `#222226` | menus, dock, dialogs |
| `surface-sunken` | `#EFEDE8` | `#E8DCC4` | `#0B0B0D` | inputs, wells, PDF backdrop |
| `reader-page` | `#FFFFFF` | `#F8F0E0` | `#1A1A1D` | reading column / page paper |
| `border` | `#E4E1DA` | `#DCCDB0` | `#2C2C31` | hairlines |
| `border-strong` | `#CFCBC2` | `#C7B391` | `#3E3E45` | input borders, dividers |
| `fg` | `#1C1B19` | `#3B2F22` | `#EDEDF0` | primary text |
| `fg-muted` | `#5E5A53` | `#6B5A45` | `#A8A8B3` | secondary text |
| `fg-subtle` | `#8C877E` | `#927E63` | `#72727D` | captions, placeholders (not body text) |
| `accent` | `#5146D9` | `#5B47C9` | `#8B85FF` | primary buttons, active nav, progress, play |
| `accent-hover` | `#4338CA` | `#4B39B3` | `#A09BFF` | hover/pressed |
| `accent-fg` | `#FFFFFF` | `#FFFFFF` | `#0F0E1A` | text/icons on accent |
| `accent-soft` | `#ECEBFC` | `#E6DDF0` | `#25234A` | selected cards, active tab bg, chips |
| `success` / `success-soft` | `#1F8A5B` / `#E3F4EC` | `#2F7A4F` / `#E1ECD6` | `#3DD68C` / `#10291E` | saved, export done |
| `warning` / `warning-soft` | `#B7791F` / `#FBF1DE` | `#A0661A` / `#F3E2C2` | `#F5B94A` / `#2E2310` | processing, already-existed |
| `danger` / `danger-soft` | `#D1453B` / `#FBE8E6` | `#B53A2F` / `#F3D9D0` | `#FF6B61` / `#331614` | errors, delete |
| `focus-ring` | `#7C74F0` | `#7C63D9` | `#A09BFF` | focus-visible outline |
| `overlay` | `rgb(20 18 15 / .45)` | `rgb(59 47 34 / .40)` | `rgb(0 0 0 / .60)` | scrim behind sheets/dialogs |
| `highlight-alpha` | `.45` | `.50` | `.30` | sentence highlight opacity multiplier |
| `word-alpha` | `.85` | `.85` | `.60` | active word highlight opacity |

### 1.2 Highlight swatches

Replace the old `SettingsOverlay` colour list. Stored as hex in
`settingsStore.highlightColor`; rendered with
`color-mix(in oklab, <hex> calc(var(--highlight-alpha) * 100%), transparent)`
so one choice works in every theme.

| Name | Hex | | Name | Hex |
|---|---|---|---|---|
| Honey *(default)* | `#FCD34D` | | Lavender | `#C4B5FD` |
| Mint | `#6EE7B7` | | Rose | `#F9A8D4` |
| Sky | `#7DD3FC` | | Coral | `#FCA5A5` |
| Peach | `#FDBA74` | | Aqua | `#67E8F9` |

Legacy stored values (`#fef08a`, `#86efac`, `#93c5fd`, …) must still load —
`isValidColor` accepts any `#RRGGBB`. `VALID_COLORS` and
`DEFAULTS.highlightColor` in `stores/settings.ts` are updated to the above.

### 1.3 Search highlight colours

Derived from `accent`, never hard-coded RGBA (replaces PDFViewer's
`SEARCH_*_COLOR` constants with CSS variables):

- Match: `accent` at 16% fill + 1px `accent` outline at 40%.
- Current match: `accent` at 32% fill + 2px solid `accent` outline.

Must stay distinguishable from the playback highlight in every theme.

### 1.4 Typography

- `font-ui`: Inter / system sans stack — all chrome.
- `font-reading`: Literata / Source Serif 4 / system serif stack — TextViewer
  body only; non-blocking load, system serif fallback.
- `font-mono`: system mono — code/IDs.
- Scale (px): `xs` 12, `sm` 14, `base` 16, `lg` 18, `xl` 20, `2xl` 24,
  `3xl` 30, `display` 36–48 (Read page title), each with line-heights.
- Tabular numerals (`tnum`) for all times and counters.
- Reading body: line-height 1.7, paragraph spacing, `text-wrap: pretty`.

### 1.5 Spacing, radius, elevation

- Spacing: Tailwind 4px scale. Page gutters 16 / 24 / 32 by breakpoint.
- Radius: `sm` 6, `md` 10, `lg` 14 (cards), `xl` 20 (dock; sheets on top
  corners), `full`.
- Elevation: `shadow-1` cards, `shadow-2` menus/dock, `shadow-3`
  dialogs/sheets. Soft, low-opacity; dark mode relies more on borders.

### 1.6 Motion

- Durations: `--dur-fast: 120ms`, `--dur-base: 200ms`, `--dur-slow: 320ms`.
- Easings: `--ease-out: cubic-bezier(.2,.8,.2,1)`,
  `--ease-in-out: cubic-bezier(.4,0,.2,1)`,
  `--ease-emph: cubic-bezier(.3,1.4,.5,1)` (sparingly).
- All motion wrapped in `motion-safe:` / zeroed under
  `prefers-reduced-motion`. Animate transform and opacity only.

### 1.7 Z-index layers (named in one place in `app.css`)

`base` < `sticky` < `dock` < `panel` < `overlay` < `dialog` < `toast` <
`tooltip`.

### 1.8 Focus

2px `focus-ring` outline with offset on every interactive element,
`focus-visible` only.

## 2. Contrast verification (WCAG, computed)

Required: `fg` and `fg-muted` ≥ 4.5:1 on `bg`, `surface`, `reader-page` in
every theme; `accent-fg` ≥ 4.5:1 on `accent`. All pass:

| Pair | Light | Sepia | Dark |
|---|---|---|---|
| `fg` on `bg` | 15.92 | 10.60 | 16.14 |
| `fg` on `surface` | 17.21 | 11.47 | 15.01 |
| `fg` on `reader-page` | 17.21 | 11.47 | 14.86 |
| `fg-muted` on `bg` | 6.34 | 5.39 | 8.01 |
| `fg-muted` on `surface` | 6.86 | 5.84 | 7.45 |
| `fg-muted` on `reader-page` | 6.86 | 5.84 | 7.37 |
| `accent-fg` on `accent` | 6.51 | 6.58 | 6.28 |
| `accent` (as text) on `bg` | 6.02 | 5.37 | 6.19 |
| `accent` (as text) on `surface` | 6.51 | 5.81 | 5.76 |

`fg-subtle` measures 3.18–3.97:1 by design — it is restricted to captions,
placeholders and disabled text, never body copy (per §8.1 "not for body
text").

## 3. Component inventory (target structure)

```
src/lib/
  ui/                       # primitives: no app knowledge, no stores, no api
    Button.svelte           # primary|secondary|ghost|danger|icon; sm|md|lg; loading; disabled; href
    IconButton.svelte       # 44x44 coarse hit area, tooltip on fine pointers, required aria-label
    Icon.svelte             # single icon source (inline SVG paths)
    Switch.svelte  Slider.svelte  SegmentedControl.svelte  Select.svelte
    TextField.svelte  TextArea.svelte
    Card.svelte  Badge.svelte  Chip.svelte
    ProgressBar.svelte      # determinate | indeterminate | shimmer
    Spinner.svelte  Skeleton.svelte  Kbd.svelte  Tooltip.svelte
    Menu.svelte             # popover menu, keyboard nav
    Dialog.svelte           # centred >=md, bottom sheet <md, same API
    Sheet.svelte            # side panel >=lg, bottom sheet w/ drag handle <md
    ConfirmDialog.svelte
    Toaster.svelte          # renders toast store
    EmptyState.svelte       # icon, title, body, action snippets
    ErrorState.svelte       # message + Retry; tone: inline|panel|page
    AsyncView.svelte        # snippets: loading/error/empty/content — ONE way to render async
    Dropzone.svelte
  stores/
    toast.ts                # push({tone, title, action?}), auto-dismiss, max 3
    ui.ts                   # sidebar collapsed, panel open, immersive mode
  components/
    shell/    AppShell  SideNav  NavRail  BottomTabBar
    reader/   ReaderShell  ReaderHeader  PlayerDock  TransportControls
              SpeedControl  ProgressScrubber  TextViewer  PDFViewer
              ZoomControl  PageNavigator  SearchBar  BookmarksPanel
              SettingsPanel  ChapterList  BackToCurrentPill
    library/  BookCard  BookGrid  ResumeCard  UploadButton
    voices/   VoiceCard  VoiceFilters
    exports/  ExportRow  NewExportSheet
```

Rules: one `ReaderShell` serves `/` reading mode and `/reader/[id]` (routes
≤ ~150 lines); every async surface goes through `AsyncView`; primitives take
`class` + spread `...rest`; variants in typed maps; no `<style>` blocks
except `:global()`; keyframes in `app.css`.

Existing → target mapping: `Sidebar`→`shell/SideNav`, `MediaBar`→
`reader/PlayerDock`+`TransportControls`+`SpeedControl`,
`AudioProgressBar`→`reader/ProgressScrubber`, `TopToolbar`→
`reader/ReaderHeader` (CC + Copy removed), `SearchOverlay`→
`reader/SearchBar`, `BookmarkPanel`→`reader/BookmarksPanel`,
`SettingsOverlay`→`reader/SettingsPanel`, `UploadDialog`→
`ui/Dropzone`+`ui/Dialog`, `LibraryCard`→`library/BookCard`,
`LastRead`→`library/ResumeCard`, `BookGrid`→`library/BookGrid`,
`PageNavigator`→`reader/PageNavigator`, `TextViewer`/`PDFViewer`→
`reader/` (PDFViewer rendering pipeline preserved).

## 4. State matrix (flow → states → component)

| Flow | States | Component(s) |
|---|---|---|
| F1 Paste → Read | empty (CTA disabled) · typing (counts, est. time) · submitting (spinner, locked) · error empty/400/backend-down · reading mode · New-text confirm | `routes/+page.svelte`, `ui/TextArea`, `ui/Button`, `ui/ConfirmDialog`, `ReaderShell` |
| F2 Save to library | unsaved · saving · saved (toast + "Open in Library") · failed (inline + toast, retry) | `routes/+page.svelte`, `ui/Button`, `stores/toast.ts` |
| F3 Upload | idle dropzone · drag-over · selected (name/size/type chip, replace/remove) · invalid type · uploading + "Processing… extracting sentences" · success → reader · already-existed toast · network error + retry | `ui/Dropzone`, `ui/Dialog`, `routes/upload/+page.svelte`, `library/UploadButton` |
| F4 Playback | loading skeleton · 404 + Back to Library · empty sentences · ready/paused · buffering (spinner + `data-loading`, shimmer, sentence breathe; never while paused) · playing · seeking (controls briefly disabled) · end · WS unavailable | `reader/PlayerDock`, `TransportControls`, `SpeedControl`, `ProgressScrubber`, `ui/Skeleton`, `ui/ErrorState`, `ui/EmptyState` |
| F5 Highlight & autoscroll | sentence highlight (colour, on/off) · word highlight · search vs current match · bionic on/off (PDF dim + overlay) · autoscroll on/off | `reader/TextViewer`, `reader/PDFViewer`, `reader/SettingsPanel` |
| F6 Page nav (PDF) | current/total · prev/next disabled at ends · jump input (validate, clamp, invalid state) · zoom in/out/reset | `reader/PageNavigator`, `reader/ZoomControl` |
| F7 Search | closed · open empty · typing · N results + current/total + prev/next (Enter/Shift+Enter) · no results · Esc clears | `reader/SearchBar` |
| F8 Bookmarks | loading · empty · list (page 1-based, label, date) · add (optimistic + toast) · add failed (surfaced) · delete + undo toast · delete failed · go-to (close + seek) | `reader/BookmarksPanel`, `stores/toast.ts` |
| F9 Settings | swatches · highlight switch · autoscroll · hotkeys · bionic + sliders when on · hotkey reference · live preview | `reader/SettingsPanel`, `ui/Switch`, `ui/Slider`, `ui/Kbd` |
| F10 Library | skeletons · backend down + Retry · empty (Upload CTA + paste link) · grid · Resume card (progress, not "Page") · delete confirm → deleting → exit animation · delete failed | `library/BookGrid`, `BookCard`, `ResumeCard`, `ui/Skeleton`, `ui/ErrorState`, `ui/EmptyState`, `ui/ConfirmDialog` |
| F11 Voices | skeletons · backend down + Retry · grouped/filterable grid · selected persists · preview idle/loading/playing/stop · preview failed toast · upload .pt · delete custom (confirm; hidden for built-in) | `voices/VoiceCard`, `VoiceFilters`, `routes/voice/+page.svelte` |
| F12 MP3 export | loading · backend down · empty CTA · rows pending/processing(%)/done(size+Download)/error(expandable message) · New Export sheet (book → voice → speed → submitting → toast) · delete confirm · silent polling | `exports/ExportRow`, `NewExportSheet`, `routes/mp3/+page.svelte` |
| F13 Shell | desktop sidebar · tablet rail · mobile bottom tabs · active route · transitions · immersive reader mode | `shell/AppShell`, `SideNav`, `NavRail`, `BottomTabBar`, `stores/ui.ts` |
| F14 Keyboard | Space, ←/→, ↑/↓, B, F, Esc · `?` sheet (reuses hotkey reference) · visible focus rings | `utils/hotkeys.ts`, `reader/SettingsPanel`, `ui/Kbd` |

Allowed enhancements (§3.3): theme (Light/Sepia/Dark/System, no-flash),
TextViewer text size + line height, EPUB chapter TOC (hide when ≤1 chapter),
Back-to-Current pill, toasts + confirm dialog primitives, `ApiError` +
`lib/utils/errors.ts` friendly copy.

## 5. Test contract (must keep working)

Buttons `Play`/`Pause`/`Read Aloud`/`Retry`/`Save`/`New text`/`Search`/
`Next match`/`Previous match`; speed `1x`/`1.5x`/`2x` in `role="group"`
"Playback speed"; `[aria-label="Settings"]`; `role="switch"`;
`#bionic-fixation`, `#bionic-ratio`; label texts `Fixation point: N`,
`Bold strength: X.XX`; `[data-sentence-index]`, `[data-highlighted="true"]`,
`[data-word-index][data-sentence-index]`, `[data-index]`, `[data-overlay]`,
`[data-bionic-overlay]`; `button[data-loading="true"]`; `.shimmer-shine`;
texts `Resume Reading`, `Could not connect to the backend`,
`Loading library…`, `No books yet`, `Upload Ebook`, `Saved`; book titles as
text (may be `h2`); `<canvas>` for PDF; `<strong>` for bionic bold.

Known planned test updates (justified when made):
`tests/error-states.spec.ts` uses `.text-red-500` (raw palette) → semantic
error selector; `tests/library.spec.ts` asserts `Loading library…` → keep as
visually-hidden text alongside skeletons, or update spec.

## 6. Brand

Wordmark **Kokoro Reader** (replaces "EbookReader"), `font-ui` semibold,
small Iris glyph; `favicon.svg` Iris mark on transparent background.
