# Gap register and implementation plans

Written after all six handoff tasks were implemented. Each entry is a gap that is
**not** currently owned by a running agent, scoped tightly enough to hand to one
implementer, with the architecture decided up front so the implementer is not also
the designer.

Process for every workstream below:

1. **Plan** (this document) — scope, approach, files, risks, acceptance.
2. **Validate** — an independent agent challenges the plan against the actual code
   and reports where it is wrong, before anyone writes code.
3. **Implement** — one agent, one workstream, isolation rules from `CLAUDE.md`.
4. **Review** — a *different* agent reviews the diff against the plan.

**Evidence labels:** **Verified** (read the code / ran it), **Inferred**,
**Reported** (an agent claimed it; not independently re-checked here).

## Current baseline (measured, 2026-09-26 ~12:45)

| Gate | Result |
|---|---|
| `pytest` (via `scripts/test.sh fast`) | **591 passed, 1 xfailed, 4 failed**, 34.7 s |
| The 4 failures | `test_system_capabilities.py::TestBackendSelection` — `AttributeError: module 'main' has no attribute '_init_remote_kokoro'`, tracked to in-flight `main.py` |
| `npm run check` | **0 errors, 12 warnings in 5 files** |
| `npm run test:unit` | **273 passed in 21 files** |
| `npm run test:e2e` | 32 `@critical` selected; **never run as a set**. Browser resolution fixed this session, verified with one test passing in 10.6 s |

Deliberately excluded from the register: anything a running agent already owns
(`main.py` backend selection, `mp3.py` export options, `engine_manager.py`,
`docs/ARCHITECTURE.md`).

---

# Workstream A — Truthful book metadata

**Gaps:** G1 (author can never be set), G3 (the card states a false unit).

### The gaps

**G1 — `Book.author` is unreachable. Verified.** No endpoint in any router writes
it. `backend/db/models.py` has `author: Optional[str] = None`; `_serialize` in
`backend/routers/library.py` returns it; the card renders it. So it is always
`NULL`, which is why the UI's normal state is "Author not detected". Handoff §3.5
asks for the user to be able to set it. This is a *feature gap*, not a bug: the
field was designed but never wired.

**G3 — the card states the wrong unit. Verified.** `LibraryCard.svelte` renders
`"{page_count} pages"`. `page_count` is not pages: for PDFs it is real pages, for
text books it is a **sentence count**, and for EPUB it is a derived
`max(1, len(raw) // 10)`. So for two of three formats the card tells the user a
number that describes something else. `GET /library` now also returns
`sentence_count`, so the honest fix is available without a new request.

### Scope

**In:** a `PATCH /library/{book_id}` accepting `title` and `author`; the card's
byline becoming editable; the unit label telling the truth.

**Out:** fetching author metadata from the file (EPUB/PDF embedded metadata) —
tempting but a separate feature with its own parser work. Out: any change to
`page_count`'s stored meaning; it is load-bearing for the reader's page index.

### Architecture

**Backend.** One new endpoint, following the existing `FolderAssignment` pattern in
the same file:

```
PATCH /library/{book_id}
body: {"title"?: str, "author"?: str | null}
```
- Both fields optional; a request with neither is a 400 rather than a silent no-op.
- `title` must be non-empty after trimming (the existing folder validator is the
  precedent: `_clean_name` in `routers/folders.py`).
- `author` accepts `null` to clear it, and `""` normalises to `null` — otherwise the
  UI can produce a stored empty string that looks like a name.
- Returns the same serialised book as `GET /library/{book_id}` so the client can
  update from the response instead of refetching.
- Cap lengths (title 300, author 200) for the same reason folder names are capped.

**Frontend.** The rename affordance already exists for folders
(`FolderNameDialog.svelte`), and the pattern is established: never validate on the
client, surface the server's `detail` via `toDetailMessage`. Author editing is the
smallest possible extension of that pattern — a second field in the same dialog
shape, reachable from the card's options menu. No new store: the library route
already refetches after mutation so counts come from the server.

**The label.** One `$derived` on the card:
```
file_type === 'pdf'  → `${page_count} pages`
otherwise            → `${sentence_count} sentences`
```
This is a **display decision, and it must not invent anything**: if
`sentence_count` is 0 or absent, render nothing rather than "0 sentences".

### Files
`backend/routers/library.py`, `backend/tests/test_library_listing.py` (extend),
`frontend/src/lib/api.ts`, `frontend/src/lib/components/LibraryCard.svelte`,
`frontend/src/routes/library/+page.svelte`, a new
`frontend/src/tests/components/library-card-metadata.test.ts`.

### Risks
- `LibraryCard`'s accessible name is `"{title} by Unknown"` and
  `frontend/tests/folders.spec.ts` selects it with `getByLabel('The Shallows by
  Unknown')` — a **@critical e2e test**. Changing the byline text breaks it. The
  implementer must update that spec in the same change and say it is unrun.
- `page_count` is used by the reader's page index (`totalPages`). Do not touch the
  field itself.

### Acceptance
- `PATCH` sets title and author; clearing author works; empty title is 400.
- A text book's card says "N sentences"; a PDF's says "N pages".
- No new warnings or errors from `npm run check`; unit tests green; the new card
  test covers both formats and the unknown-count case.

---

# Workstream B — One source of truth for the reading position

**Gap:** G2. **This is the most user-visible defect outstanding.**

### The gap — Reported, and consistent with the code I read

`reader.currentIndex` only moves on an explicit `seek()`. `audio.currentIndex`
advances as playback proceeds. `TextViewer` highlights from the audio store and
`PDFViewer` autoscrolls from it, so **the reading surface is correct** — but the
page indicator (Workstream A's sibling, tasks 4) and the *saved progress* follow
`reader.currentIndex`. So while a book plays continuously, the saved position and
the page indicator fall further behind, and a reload resumes from somewhere the
user finished listening to several minutes ago.

**The implementer must verify this before changing anything.** The plan is written
against a reported behaviour, and this repo has a history of specs describing
things that were not true.

### Scope

**In:** making the saved position and the page indicator follow actual playback;
persisting progress without a write per sentence.

**Out:** changing which store the highlight uses. `audio.currentIndex` driving the
highlight is correct and is covered by e2e (`highlight-sync.spec.ts`). Touch
nothing there. Out: any change to the WebSocket protocol.

### Architecture

Decide, and state in the code, that **the audio store is the source of truth for
"where playback is"** and the reader store is the source of truth for "where the
user asked to be". Then:

- On each advance, mirror the audio index into `reader.currentIndex` — but **only
  while playing**, so a paused seek is not overwritten by a late event.
- Persist progress on a **debounce/throttle**, not per sentence: the existing
  `saveProgress` is an HTTP POST, and one per sentence at 1.0× is a request every
  few seconds. Write on: index change with a minimum interval, on pause, on
  unmount, and on `beforeunload`. This is the part most likely to be got wrong.
- **Do not** create a new store. The handoff forbids parallel state, and
  `reader.ts`/`audio.ts` already own these two concerns.

### Risks — the highest of any workstream here
- `stores/audio.ts` contains a **generation counter and a decode chain** that
  exist to stop stale events advancing the index (documented in `CLAUDE.md`).
  Anything that writes back into that path can reintroduce the seek-backward bug
  those guards fix. The implementer must read the comments around `generation` and
  `scheduleChunk` before editing, and must not weaken a guard to make a test pass.
- `admin`-free: no protocol change means the e2e `highlight-sync` suite should be
  unaffected — if it is not, that is a signal the change was too invasive.
- Throttled saves can lose the final position if unmount is missed. The unmount
  and visibility paths are part of the acceptance, not optional polish.

### Acceptance
- Playing continuously for 30+ s then reloading resumes within one sentence of
  where playback actually was (state the measured tolerance; do not claim "exact").
- Seeking while paused still resumes where the user seeked.
- Progress POSTs are visibly fewer than sentences played (measure and report the
  count).
- `highlight-sync.spec.ts` behaviour unchanged; `npm run test:unit` green.

---

# Workstream C — Close the test gates

**Gaps:** G4 (e2e never run as a set; 4 known failures), G5 (route-level composition
untested).

### The gaps

**G4a — 32 critical e2e tests have never been run as a set. Verified.** The browser
resolution is fixed (one test passed in 10.6 s), but the suite as a whole is unrun.

**G4b — `frontend/tests/bionic-reading.spec.ts` was edited and never run.
Reported.** An agent replaced all 15 positional `getByRole('switch').nth(3)`
queries with named ones and added 2 tests, and says the edits are unrun and one new
test is unverified.

**G4c — 4 pre-existing failures outside the critical set. Reported**, verified
pre-existing by A/B re-run with the change reverted: 3 in `buffering-states.spec.ts`
(`data-loading` / word-div selectors) and 1 in `text-reader.spec.ts` (strict-mode
collision between the Upload button and the Sidebar's Upload nav item).

**G5 — the `handlePageJump → handleSeek` composition is untested at route level.
Reported.** The unit test covers `seek(20)` at store level and the round trip, but
nothing covers the route wiring that connects a page click to `seek()`.

### Scope and architecture

This is a **verification** workstream, not a feature one. Do these in order, and
stop and report rather than "fixing" anything ambiguous:

1. Run `npm run test:e2e` (all 32 critical) and report the raw result.
2. Run `E2E_ALL=1 npm run test:e2e` and report the full result, separating
   pre-existing failures from new ones.
3. For each failure, classify: **new** (a regression from this session's work —
   must be fixed), **pre-existing** (fix if the cause is clear and local, otherwise
   report), or **flaky** (re-run to confirm; report the flake rate).
4. G4b specifically: confirm the bionic spec passes now. If the new "common words"
   test is wrong, fix the test, not the implementation.
5. G5: add ONE `@critical` e2e test for the reader's page jump. It is critical by
   the stated bar only if the page indicator is load-bearing for reading; if the
   implementer concludes it is an enhancement, say so and skip it, with the reason.

**The 4 pre-existing failures must be resolved one way or the other** — either
fixed, or deleted with a written justification, or left with the reason recorded
in `frontend/tests/README.md`. What is not acceptable is a suite that is red by
default and that everyone has learned to ignore.

### Files
`frontend/tests/*.spec.ts`, `frontend/tests/README.md`, `frontend/playwright.config.ts`
only if the browser resolution needs adjusting.

### Risks
- This workstream and any other frontend workstream conflict by touching specs.
  It must run **alone**, after A's spec edit lands.
- Deleting a test to make a suite green is the failure mode to guard against. Every
  deletion needs a justification a reviewer will accept.

### Acceptance
- A written pass/fail table for all 121 tests, with each failure classified.
- `npm run test:e2e` (32 critical) green, or every remaining red item individually
  justified.
- The bionic spec's verdict stated plainly.

---

# Workstream D — Extract the shared UI primitives DESIGN.md promises

**Gap:** G8. Objective item 4 ("extract shared components, leave code cleaner").

### The gap — Verified

`frontend/DESIGN.md` §3 describes `ui/Button`, `ui/Dialog`, `ui/Switch`,
`ui/Toaster`, `ui/EmptyState`, `ui/AsyncView`. **None exist.** An agent noted
`ui/Switch.svelte` was referenced but absent. The same markup is therefore repeated:
four dialogs each re-implement the scrim + `use:overlayLayer` + focus restore
(`SettingsOverlay`, `UploadDialog`, `FolderNameDialog`, `MoveToFolderDialog`), the
button class string is repeated across components, and `SettingsOverlay` carries a
local `{#snippet switchRow(...)}` that is exactly the missing `Switch`.

### Scope

**In:** `Button`, `Dialog`, `Switch`, `EmptyState` — the four with real duplication
and existing precedent to copy from.

**Out:** `AsyncView` (the loading/error/empty state machine is genuinely different per
route and there is no second consumer yet), `Toaster` (already exists), and **any
visual restyling**. This is a refactor: the rendered output must not change. That is
testable — see acceptance.

### Architecture

- One primitive per file under `frontend/src/lib/components/ui/`.
- `Dialog` must wrap the **existing** `use:overlayLayer` action and
  `overlay-stack` utility, not replace them. Those encode the handoff's §4 rules
  (Escape closes only the topmost layer, focus moves in and is restored) and are
  already covered by tests. The refactor is about removing repetition, not
  redesigning the mechanism.
- `Button` takes a `variant` (`primary`/`secondary`/`danger`/`ghost`) and forwards
  the rest, so call sites shrink to `<Button variant="primary" onclick={...}>`.
- `Switch` owns the label association, the 44 px hit area and the ARIA wiring that
  were just fixed by hand. **This is the highest-value extraction**: the
  `role="switch"` accessible-name landmine was fixed manually this session and
  lived in the fact that each switch was bespoke.
- Svelte 5: `$props()`, snippets for content, callback props. No `@apply`.

### Risks
- 273 unit tests mount these components. A refactor that changes rendered markup
  breaks them; that is the signal to check, not to weaken the tests.
- The `getByRole('switch', { name: ... })` selectors in `bionic-reading.spec.ts`
  depend on the label association `Switch` will now own. Preserve it exactly.
- Do **one primitive per commit** so a regression is bisectable.

### Acceptance
- Duplication measurably reduced: report the before/after count of the repeated
  scrim/overlay block and the repeated button class string.
- `npm run check` shows **no new warnings** (12 is the baseline) and 0 errors.
- `npm run test:unit` **273 tests still passing, unmodified**. If a test needs
  changing, that is a behaviour change and must be reported as one.
- No visual change: state which spec or screenshot check supports that.

---

# Workstream E — Wire Modal batching and warm-up into the reader

**Gap:** G6. Reported by the Modal agent as deliberately deferred.

### The gap

The deployed Modal function accepts `texts: list[str]` and the client has
`synthesize_many()` and `warmup()` (a non-blocking `.spawn()`). Measured: 3
sentences batched in 1.09 s vs 0.50 s for one, and `warmup()` returned in 0.151 s.
Neither is used by the reader, because wiring them needs `routers/tts.py` /
`services/tts_engine.py`, which the Modal agent was forbidden to edit.

### Scope

**In:** a warm-up spawn when a book is opened, and using `synthesize_many` inside
the prefetch window when the active backend is remote.

**Out:** any change to the local path's behaviour, and any change to the audio cache
contract. **Out: making the reader depend on the remote backend** — the local path
must remain fully functional and default.

### Architecture

- Warm-up belongs where a book is opened, once: `routers/tts.py` on the first
  `play`/`seek` for a `book_id`, guarded so it fires once per connection.
- `synthesize_many` fits the **prefetch** path only, which already has an audio-time
  budget and walks sentences in order — a batch maps onto one window naturally.
  `stream_job` must keep synthesising one sentence at a time: it streams to a
  listener who is waiting, and must not block on a batch.
- Gate on the resolved transport being remote. A local `synthesize_many` would be
  serial anyway and would only add a code path.
- The per-sentence cache contract is unchanged: every sentence still lands under its
  own key.

### Risks
- This is the only workstream that can cost the user money if it misbehaves (GPU
  seconds). It must not spawn warm-ups on every sentence.
- `tts_engine.prefetch` currently holds one worker and yields to playback. Batching
  must not lengthen the window it holds that worker.
- No GPU is reachable in this sandbox, so the implementer **cannot** verify the
  remote path end to end. Say so.

### Acceptance
- Local-only runs behave identically (state how that was checked).
- Warm-up fires once per connection, proven by a test with a fake client counting
  calls.
- Prefetch uses `synthesize_many` only when the backend is remote, proven by a test.
- The remote end-to-end path is explicitly labelled **unverified in this sandbox**.

---

# Workstream F — Stop reloading spaCy per engine construction

**Gap:** G13. Performance, not correctness — but it taxes every test run.

### The gap — Measured by an agent, consistent with the code I read

`BaseEngine.__init__` calls `spacy.load("en_core_web_sm")` on **every construction**,
and engines are constructed per test and per extraction. `scripts/test.sh fast`
takes 40 s, of which a large part is repeated model loading. The same cost is paid
in production on every engine instantiation.

### Scope

**In:** load the pipeline once per process and reuse it.
**Out:** changing extraction behaviour, threading the engines, or touching the
download fallback (already fixed this session).

### Architecture

A module-level lazy singleton:
```
_nlp = None
def _get_nlp():
    global _npl
    if _npl is None: _npl = spacy.load(...)
    return _npl
```
with a lock, because `routers/tts.py` and the export path can construct engines from
different threads. A bare module-level `spacy.load` at import time is **not**
acceptable: it would make importing the module fail when the model is missing, and
it would run the download fallback during import.

spaCy pipelines are documented as usable across threads for inference; the
implementer must confirm that claim for this version rather than assuming it, and
must report what they found.

### Risks
- A shared pipeline is shared mutable state. If any caller mutates `nlp`, that now
  leaks. Audit for mutation before sharing.
- Long-lived model + the eviction/threading work already landed: a shared pipeline
  must not become a hidden global that tests cannot isolate. Keep it patchable (the
  conftest already fakes G2P; it may need to fake this too).

### Acceptance
- Measured before/after wall clock for `scripts/test.sh fast`, reported honestly
  (the machine is shared, so run each at least twice and say so).
- `test_base_engine.py`, `test_pdf_engine.py`, `test_epub_ocr_engines.py` green.
- The threading claim is either verified or flagged as unverified.

---

## Dependency order and isolation

```
A (metadata)      ── independent ── can run in parallel with D, F
B (position)      ── independent, highest risk ── run ALONE
C (e2e closure)   ── must run AFTER A edits folders.spec.ts and after D settles
D (ui primitives) ── touches many components ── run ALONE
E (modal wiring)  ── backend only ── can run in parallel with A/D/F
F (spacy cache)   ── backend only, small ── can run in parallel
```

**Every implementer must read `CLAUDE.md`'s "Working with other agents" section
first.** Never `git stash`, `git checkout --`, `git restore`, `git reset` or
`git clean` in the shared tree; use `scripts/agent-worktree.sh` when a workstream
needs isolation; commit frequently.

**Backend workstreams (E, F) and frontend workstreams (A, B, D) do not overlap.**
C touches only specs and must not run concurrently with A or D.
