# Implementation handoff — Kokoro Reader prototype → real app

**What this folder is:** a high-fidelity HTML prototype. It is the *visual and behavioural
spec*: layout, states, copy, interaction rules. It is **not** the implementation and its
data layer is simulated.

**What is real:** `/home/christapia50/Repos/ebook-reader` — FastAPI + SQLModel backend,
SvelteKit frontend. That repo is the system of record. When this document and the repo
disagree, the repo wins.

> **Path note:** the repo lives at `/home/christapia50/Repos/ebook-reader`. The spelling
> `christiapia50` (with an extra `pia`) appears in some task blocks and does not resolve.

---

## 1 · Ground rules for the implementing agent

1. **Extend, never rebuild.** Read the existing file before writing a line. Several things
   this prototype "invents" already exist and must be reused.
2. **Reuse the API client.** `frontend/src/lib/api.ts` is the only place HTTP lives. Add
   functions there; never `fetch` from a component.
3. **Reuse the stores.** `$lib/stores/settings.ts` already holds `bionicMode`,
   `bionicFixation`, `bionicBoldRatio`, `highlightColor`, `autoscroll`, `hotkeysEnabled`,
   `highlightEnabled`, `theme`. Do not add a second store for any of these.
4. **Svelte 5 runes** (`$props`, `$state`) — match `lib/components/SettingsOverlay.svelte`.
5. **Design language** is `frontend/DESIGN.md` plus the token names the components already
   use. Do not introduce a new palette.
6. **Tests are part of done:** `npm run test:unit` (vitest), `npm run test:e2e` (playwright),
   `npm run check` (svelte-check), `pytest` in `backend/`. All must pass.
7. **Never port the prototype's simulations.** No fake progress timers, no invented metrics,
   no demo seeds, no `localStorage` shadow copies of server state.

---

## 2 · Page-by-page mapping

| Prototype file | Real target | Endpoints that already exist | Work required |
|---|---|---|---|
| `index.html` (library) | `routes/library/+page.svelte` + `lib/components/BookGrid.svelte`, `LibraryCard.svelte` | `GET /library`, `DELETE /library/{book_id}` | folders (net-new), card states, resume, progress bar |
| `library.js` (library logic) | split into the route + components | same | see §3, §4 |
| `pipeline.js` (upload flow) | `routes/upload/+page.svelte`, `lib/components/UploadDialog.svelte` | `POST /documents/upload`, `POST /documents/text`, `PATCH /documents/text/{id}` | staged progress, success/warning/error screens |
| `reader.html` | `routes/reader/[id]/+page.svelte` + `TextViewer.svelte`, `PageNavigator.svelte` | `GET /documents/{book_id}/sentences`, `GET|POST /library/{book_id}/progress` | pages overlay, page indicator, real progress persistence |
| `voices.html` | `routes/voice/+page.svelte` | `GET /voices`, `GET /voices/preview/{voice_id}` (audio/wav), `POST /voices/upload`, `DELETE /voices/{id}` | preview player on real audio, waveform animation |
| `settings.html` | `lib/components/SettingsOverlay.svelte` (+ new Processing section) | `GET|POST /user/settings` | processing-engine selector, bionic config, Modal connection |
| `exports.html` | `routes/mp3/+page.svelte` | `POST /mp3/export`, `GET /mp3/exports`, `GET /mp3/exports/{id}/status`, `GET /mp3/downloads/{id}`, `DELETE /mp3/exports/{id}` | progress/mid-export states, error screens |
| `paste.html` | `routes/+page.svelte` (existing paste flow) | `POST /documents/text`, `PATCH /documents/text/{id}` | empty/error states only |
| `design.html` | documentation | — | port as `frontend/DESIGN.md` appendix, not a route |

---

## 3 · Data-contract corrections (read this before coding)

These are the places the prototype's simulated layer must **not** be copied.

### 3.1 Reading progress — ALREADY IMPLEMENTED, do not rebuild
`frontend/src/lib/stores/reader.ts` already owns this:
`loadBook(bookId)` calls `getProgress(bookId)` to restore `currentIndex`, and `seek(index)`
calls `saveProgress(bookId, index)` plus `userStore.updateLastRead(...)`. The reader route
subscribes to that store and routes every navigation through `seek`.

Reuse `reader.currentIndex` / `reader.sentences`; never add a second source of truth.

The prototype invented `localStorage['kokoro:progress:<title>']` holding a **0–100 percent**
plus a `?pct=` link param. **Do not copy any of it.** The real contract is a **sentence index**
backed by `Progress(book_id, sentence_index, updated_at)` and
`UserSettings.last_book_id` / `last_sentence_index`.

Two genuine nuances worth fixing while in there:
- A progress bar is a *display* concern: derive the percentage from
  `currentIndex / sentences.length` at render time; never persist a percentage.
- "Finished" is `currentIndex === sentences.length - 1`, not `100`. The prototype had to add
  `+1` to its ratio to make the last sentence reach 100%; with a sentence index that special
  case disappears.

### 3.2 Voice preview — ALREADY PLAYS REAL AUDIO, do not rebuild
`routes/voice/+page.svelte` already calls `previewVoice(voiceId)` (backend returns
`audio/wav`), wraps it in an object URL and plays it through an `<audio>` element, tracks
`previewingId`, and cleans up on `ended`. The prototype's timed simulation was a placeholder
**for the prototype only**.

What the prototype actually contributes here is the **UI**: the persistent preview dock
(voice name + sample line, animated waveform, determinate progress from real `timeupdate`,
play/pause/stop, a single "Use this voice" primary action) and the per-card equalizer.
Port the panel and the animation onto the existing audio path — buffer caching, seek/scrub and
volume are *not* required.

### 3.3 Folders — net-new, needs backend work
There is no folder concept in either app (`grep -ri folder backend frontend/src` → nothing).
Real implementation needs, in this order:
1. `Folder` SQLModel table (`id`, `name` unique case-insensitive, `created_at`) and a
   nullable `folder_id` FK on `Book`.
2. Endpoints: `GET /folders`, `POST /folders`, `PATCH /folders/{id}`, `DELETE /folders/{id}`,
   `POST /library/{book_id}/folder` (set/clear).
3. Client functions in `api.ts`.
4. Only then the UI: folder tiles, breadcrumb, drag-and-drop, move dialog.
Keep the prototype's validation rules: blank name rejected, duplicate name (case-insensitive)
rejected, name length capped, delete keeps the books and is undoable from the toast.

### 3.4 Processing engine — partly real
`backend/main.py` already detects the device: `device = "cuda" if torch.cuda.is_available()
else "cpu"`. That is the *only* real engine choice today.
- **CPU / GPU:** wire the selector to a real capability probe. Do not fake a "Needs CUDA" pill —
  report what the probe actually returns.
- **Cloud · Modal GPU:** **no backend, no credentials, no code.** This is net-new infrastructure
  (a Modal app, a deploy, a token, a billing decision). Ship CPU/GPU now and keep Cloud behind
  an explicit product decision — do not stub an endpoint that pretends to work.

> **Amended by direct instruction:** Modal credentials *do* exist in this environment
> (`~/.modal.toml`, profile `christapi2099`, CLI 1.5.1, two apps already deployed). Cloud
> offload is therefore in scope to build **for real** — deployed and verified, never faked.
> See §9.

### 3.5 Book metadata states
`Book.author` is already `Optional[str] = None`. "Author not detected" maps to `author === null`
— render the prototype's state and let the user set it via `PATCH` (add the endpoint if absent).
Cover: `Book.cover_page: int` exists; the prototype's six typographic palettes are a
**placeholder for artwork the app cannot yet derive** — do not ship them as if they were covers.

---

## 4 · Behavioural contracts worth porting verbatim

- **Bionic reading is idempotent.** `$lib/utils/bionic-reading.ts` has the algorithm; the
  renderer must cache each word's plain text on the element (`data-*`) and always re-render
  from that cache. Toggling on→off→on N times must produce markup byte-identical to a single
  toggle on. Never `innerHTML` off the already-bolded DOM.
- **One primary action per view.** Dialogs have exactly one filled button; everything else is
  secondary/ghost.
- **Error copy is specific.** Not "something went wrong": name the failing stage and the reason
  (see the prototype's pipeline strings, e.g. "Failed at Extracting text · No text layer found").
  Backend already distinguishes 400 unsupported / 413 too large / 500 server — keep that fidelity.
- **Overlay accessibility:** Escape closes only the topmost layer; focus moves in and returns to
  the trigger; the reading column is scroll-locked by class, never `body { overflow: hidden }`.
- **Touch targets** ≥44px; card controls are 32px by default and must be bumped on touch.

---

## 5 · Suggested task breakdown (one harness run each)

Each task is sized to one agent turn and has its own acceptance check. Do them in order —
later tasks depend on earlier ones.

1. **Folders backend.** Model + migration + endpoints + pytest coverage (create/rename/delete,
   duplicate name, delete keeps books).
2. **Library folders UI.** Tiles, breadcrumb, empty-folder state, move dialog, drag-and-drop,
   validation errors. Depends on 1.
3. **Library card states.** Progress bar derived from real progress, author-missing state,
   cover placeholder, resume button. Read the value through the existing reader/user stores —
   see §3.1; no new persistence.
4. **Reader pages overlay.** Page indicator + skim overlay only. Reuse the route's existing
   page notion (`totalPages = bookMeta?.page_count ?? reader.sentences.length`) and
   `reader.currentIndex`; jump via the existing `seek()`. Round-trip test: seek → reload →
   same sentence. **No progress work — that is already done.**
5. **Voice preview panel.** Upgrade the existing preview with the dock, waveform animation,
   play/pause/stop and one "Use this voice" action, on top of the current `previewVoice` path.
6. **Settings: processing engine + bionic config.** Device probe, real engine selection, bionic
   controls bound to the existing settings store and `PATCH /user/settings`.

---

## 6 · Kickoff prompt template

Paste this per task, attaching the repo and (optionally) the matching prototype file.

```
Repo: /home/christia50/Repos/ebook-reader  (FastAPI + SQLModel backend, SvelteKit 5 frontend)
Task: <one item from §5>

Read first, in this order:
- frontend/src/lib/api.ts            (the only place HTTP lives)
- frontend/src/lib/components/<closest existing component>
- frontend/DESIGN.md                 (tokens and rules)
- backend/routers/<relevant>.py, backend/db/models.py
- implementation-handoff.md          (this document) — §3 lists contracts NOT to copy
- implementation-handoff.md §9       (verified repo facts + corrections)

Deliver:
- the smallest diff that satisfies the task, matching existing code style
- unit tests for new pure logic, and an e2e test for the new user flow
- `npm run check`, `npm run test:unit`, `npm run test:e2e`, `pytest` all green

Constraints:
- extend existing api.ts / stores / components; do not create parallel state
- no simulated data, no fake progress, no invented metrics
- one primary action per view; ≥44px touch targets; Escape closes only the top layer
- do not add a dependency without saying why

Report: files changed, the exact commands you ran, their results, and anything you could not
verify. If a requirement in this handoff contradicts the repo, say so instead of guessing.
```

---

## 7 · What to attach when you feed this in

- The **repo** (the agent must work in it, not in this folder).
- This document.
- For visual fidelity, the **one prototype file** for the task at hand — e.g. task 2 gets
  `index.html` + `library.js` for folder/tile/drag states, task 4 gets `reader.html` for the
  pages overlay. Attaching all eleven files at once dilutes the spec; attach per task.
- Do **not** attach `design.html` as a spec for shippable UI — it is the token/primitive
  reference, and the corresponding rules belong in `frontend/DESIGN.md`.

## 8 · Known traps

- **Check before you build.** Progress persistence, voice-preview audio, the bionic algorithm,
  the settings store, bookmarks and MP3 export all already exist. The cheapest way to waste a
  turn here is to rebuild one of them from the prototype. Grep first.
- `$lib/stores/reader.ts` owns `currentIndex`; `$lib/stores/audio.ts` owns playback;
  `$lib/stores/user.ts` mirrors last-read. Do not introduce a fourth place to hold position.
- `API_BASE` is hardcoded to `http://localhost:8000` in `api.ts` — fine for dev, must be
  env-driven before any deploy.
- The prototype's `?pct=` link contract is **not** what the app should use (§3.1).
- Bionic settings already persist to the server via `updateUserSettings` — check what the
  backend actually stores before adding fields, and migrate rather than duplicate. **(See §9 —
  this claim is false as written.)**
- `Book.cover_page` is a page index, not artwork; do not conflate it with a cover image.

---

## 9 · Verified repo facts and corrections

Everything below was checked against the working tree at branch `folders-and-pages`
(commit `3f7df0b`). Where this section and the text above disagree, **this section wins** —
it is the result of reading the code, and the acceptance criteria in §6 require reporting
contradictions rather than guessing.

### 9.1 Claims confirmed TRUE (do not rebuild these)

| Claim | Evidence |
|---|---|
| §3.1 progress is owned by `reader.ts` | `stores/reader.ts:23` `loadBook` → `getProgress`; `:39` `seek` → `saveProgress` + `userStore.updateLastRead`; `:43` index is clamped to `sentences.length - 1` |
| §3.2 voice preview plays real audio | `routes/voice/+page.svelte:45` `previewVoice` → `:47` `URL.createObjectURL` → `:49` `new Audio(url)` → `:50` `onended` revokes the URL; `:41-43` cleanly replaces an in-flight preview |
| §1.3 settings store fields | `stores/settings.ts:23-33` `SettingsState` has every listed field |
| §3.5 `Book.author` is `Optional[str] = None` | `db/models.py:7` |
| §3.3 no folder concept exists | `grep -rni folder frontend/src backend` → only an unrelated comment in `lib/index.ts` |
| §5.4 the route's page notion | `totalPages` derives from book meta with a `sentences.length` fallback |

### 9.2 Claims that are FALSE or incomplete

**§8 "Bionic settings already persist to the server via `updateUserSettings`." — FALSE.**
`backend/routers/user.py:15-19` accepts only `last_book_id`, `last_sentence_index`,
`highlight_enabled`; `db/models.py:67-71` `UserSettings` stores only those three columns.
`settings.ts:106` persists the whole settings blob to `localStorage['kokoro-settings']`,
and only `highlightEnabled` is ever sent to the server (`settings.ts:171`).
**Consequence:** task 6 must add real columns + a migration before bionic config can persist
server-side. Do not assume an existing field.

**§3.4 "wire the selector to a real capability probe." — no such probe is reachable.**
`backend/main.py:22` computes `device` inside `_init_kokoro()`, uses it once for
`KPipeline(...)`, and discards it. Nothing stores or exposes it, and no endpoint reports
device or capabilities. The value is also trapped inside a broad `except Exception` that
returns `kokoro = None` while the app still starts (`main.py:25-27`, `lifespan:34-36`).
**Consequence:** the probe must be captured at startup and exposed on a new endpoint.

**§3.4 "no credentials" for Modal — FALSE in this environment.**
`~/.modal.toml` holds an active profile (`christapi2099`) with `token_id`/`token_secret`;
`modal` CLI 1.5.1 is on `PATH` at `~/.local/bin/modal` and `modal app list` succeeds,
showing two already-deployed apps. Cloud offload is buildable and deployable for real.

**The run sheet's `./start.sh` prerequisite — cannot run on a fresh clone.**
`start.sh:35-40` hard-requires `backend/venv`, which **does not exist** in this checkout.
Until it is created, `pytest` cannot run and `test:e2e` has no backend at `:8000`, so two of
the four gates fail for environmental reasons. `frontend/node_modules` *is* installed.

**The run sheet's `git checkout -b folders-and-pages`** — the branch did not exist; it has
now been created from `main`.

### 9.3 Environment facts

| Fact | Value |
|---|---|
| Working branch | `folders-and-pages` (from `main` @ `e8dde98`) |
| Python | only **3.12.3** present; `CLAUDE.md` documents 3.11 and no venv exists |
| Host GPU | **NVIDIA T600 Laptop GPU**, 4 GB, Turing (cc 7.5), driver 580.178.04, kernel modules `nvidia`/`nvidia_uvm`/`nvidia_drm` loaded, `/proc/driver/nvidia/gpus/` present |
| GPU inside this agent sandbox | **Not usable** — `/dev/nvidia*` and `/dev/dri` are absent (restricted device namespace). `nvidia-smi` reports it cannot communicate with the driver, and `torch.cuda.is_available()` will read `False` *here*. The same code run by the user outside the sandbox should see the GPU. GPU-path claims verified only inside the sandbox must be labelled as such. |
| Modal | CLI 1.5.1, profile `christapi2099`, authenticated, apps `cosyvoice3-*` and `whisper-turbo-*` deployed |
| Disk / network | 537 GB free; pypi, download.pytorch.org, registry.npmjs.org all reachable |

### 9.4 Working-tree corrections applied before feature work

- `deb/` contained a **full duplicate copy of `backend/` and `frontend/`** (71 files) checked
  into git under `deb/usr/share/kokoro-reader/`. It was deleted from the working tree but the
  deletion was uncommitted. Committed separately as `671dc63` because it is unrelated to this
  feature work and would otherwise swamp every review diff.
- The pre-existing uncommitted work (8 modified backend files, 16 modified/new frontend files)
  was committed as an isolated baseline `3f7df0b`, so that feature commits read as small,
  reviewable diffs on top.
- `.gitignore` now also excludes `test-results/`, `playwright-report/`, `blob-report/` and
  `.codegraph/`, which were sitting untracked in the tree.
