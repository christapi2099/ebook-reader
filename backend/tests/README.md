# Backend test suite — inventory, hazards and how to run it selectively

**Run this instead of bare `pytest`:**

```bash
./scripts/test.sh fast          # ~40 s, the default check after a small change
./scripts/test.sh backend tests/test_tts_engine.py
./scripts/test.sh --help
```

Everything below explains what is in the suite, what is dangerous, what is
already broken, and why some of it is slow.

> Measurements in this document were taken on 2026-09-26 between 11:20 and 12:35
> on this checkout. Several agents edit this repo at once, so **counts and the
> failing baseline drift** — one run during this investigation reported 27
> failures that were gone, unfixed, four minutes later. Re-measure with
> `./scripts/test.sh fast` before trusting a number, and check whether a failing
> file is one somebody is currently editing.
>
> Timing note: the numbers below were taken under heavy CPU contention (load
> average 8–13, other agents running their own suites). They are pessimistic;
> on an idle machine `fast` measured 45.8 s and later 40 s for the same set.

---

## 1 · Inventory

29 files, 560 tests. "Cost" is the wall time of that file alone under the
current `conftest.py`, including ~4 s of interpreter start-up.

| File | Tests | What it actually needs | Cost | Hazards |
|---|---:|---|---:|---|
| `test_audio_cache_eviction.py` | 41 | temp-file SQLite via `create_engine_and_tables`, async | ~5 s | none after the conftest guard |
| `test_base_engine.py` | 9 | **real spaCy model**, loaded once *per test* | ~11 s | slow-ish; see §3.4 |
| `test_bookmarks.py` | 8 | ASGI app + in-memory DB | ~5 s | none |
| `test_db_models.py` | 6 | in-memory SQLite | ~5 s | none |
| `test_db_schema_startup.py` | 7 | schema/migration, spawns a fresh interpreter | ~7 s | writes only to `tmp_path` |
| `test_epub_ocr_engines.py` | 7 | **real EPUB + full-book spaCy NLP** | **~25 s** | reads `~/Documents/EBooks`; in `SLOW_TESTS` |
| `test_folders.py` | 33 | ASGI app + in-memory DB | ~6 s | none |
| `test_library_delete.py` | 5 | ASGI app + in-memory DB | ~5 s | none |
| `test_modal_remote.py` | 68 | pure unit, transports faked, **no network** | ~5 s | none |
| `test_mp3_export.py` | 8 | ASGI app, temp export files | ~5 s | none |
| `test_mp3_export_nonblocking.py` | 24 | threads / event loop, temp files | ~6 s | none |
| `test_pdf_engine.py` | 18 | **real PDF + full-book spaCy NLP** | **~50 s** | reads `~/Documents/EBooks`; in `SLOW_TESTS` |
| `test_prefetch_and_cache.py` | 9 | async, fake Kokoro, in-memory DB | ~7 s | none |
| `test_routers.py` | 14 | ASGI app + in-memory DB | ~5 s | none |
| `test_speed_adversarial.py` | 14 | async, fake Kokoro | ~7 s | none |
| `test_speed_engine.py` | 60 | async, fake Kokoro, in-memory DB | ~5 s | none |
| `test_speed_unavailable_notice.py` | 3 | WebSocket + fake Kokoro | ~5 s | none |
| `test_system_capabilities.py` | 27 | `main` module globals + device probe | ~5 s | monkeypatches `main` internals; fragile while `main.py` is edited |
| `test_text_endpoint.py` | 17 | ASGI app + in-memory DB | ~12 s | none |
| `test_text_engine.py` | 8 | **real spaCy model**, loaded once *per test* | ~11 s | slow-ish; see §3.4 |
| `test_text_filter.py` | 42 | **pure unit** — no I/O at all | ~5 s | none |
| `test_tts_engine.py` | 38 | pure unit, fake Kokoro | ~7 s | none |
| `test_user_settings.py` | 6 | ASGI app + in-memory DB | ~5 s | none |
| `test_voice_change.py` | 4 | WebSocket, temp DB file | ~8 s | none |
| `test_voices_path_traversal.py` | 34 | router security, temp dirs | ~5 s | none |
| `test_voices_router.py` | 11 | ASGI app + `voices/` dir | ~5 s | writes voice files — to `tmp_path` since the conftest guard |
| `test_websocket_integration.py` | 24 | WebSocket, deliberate sleeps | ~17 s | slowest *green* file |
| `test_word_timestamps.py` | 7 | pure unit, fake Kokoro | ~5 s | none |
| `test_ws_mimo.py` | 8 | WebSocket, in-memory DB | ~8 s | none |

Categories, if you prefer them grouped:

* **Pure unit, no I/O** — `test_text_filter`, `test_modal_remote`, `test_tts_engine`,
  `test_word_timestamps`, `test_speed_engine`, `test_speed_adversarial`.
* **DB-backed (in-memory)** — `test_db_models`, `test_db_schema_startup`,
  `test_audio_cache_eviction`, and every ASGI test through the `client` fixture.
* **ASGI / WebSocket through `TestClient`** — the majority of the list above.
* **Real filesystem corpus** — `test_pdf_engine`, `test_epub_ocr_engines`
  (and `test_base_engine` / `test_text_engine` load the real spaCy model).
* **Genuinely slow** — `test_pdf_engine`, `test_epub_ocr_engines`, then
  `test_websocket_integration`.

---

## 2 · The 600-second "hang", explained

It was never a deadlock. Two files run real NLP over a whole book with no
per-file caching, and the rest of the suite used to pay a real model load per
test on top of that.

Measured, in isolation, on this machine:

| Operation | Measured |
|---|---:|
| `PDFEngine.extract_sentences(cleancodebook.pdf)` → 10 217 sentences | **120.2 s** |
| `EPUBEngine.extract_sentences(cleancodebook.epub)` → 6 831 sentences | **47.6 s** |
| `import kokoro` (which imports torch) | 5.7 s |
| `KPipeline(...)` construction | 2.5 s |
| entering `TestClient(app)` lifespan — cold | 12.2 s |
| entering `TestClient(app)` lifespan — warm | 5.4 s |
| `create_engine_and_tables()` against the real 839 MB DB | 0.04 s |

`test_pdf_engine.py` calls `extract_sentences` **9 times** and
`test_epub_ocr_engines.py` **6 times**. At the numbers above that is ~18 minutes
and ~5 minutes respectively — far past any reasonable timeout, which is exactly
what "hung and was SIGTERM-killed after 600 s" looked like.

Two things have since changed, both owned by the test-repair change:

1. The extraction is now cached in a fixture, so each file pays it **once**
   (50 s and 25 s) instead of once per test.
2. `conftest.py` installs an autouse guard with a **fake Kokoro**, so no test
   builds a real `KPipeline` any more. `test_voices_router.py` alone went from
   ~187 s to ~5 s.

Current cost of the pair: **114 s** (`./scripts/test.sh slow`). They are still
the only reason `full` is not fast, so they stay in `SLOW_TESTS`.

### 2.1 A second, latent hang

`services/base_engine.py` constructs spaCy like this:

```python
try:
    self.nlp = spacy.load("en_core_web_sm")
except OSError:
    import subprocess
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"],
                   capture_output=True)          # no timeout
    self.nlp = spacy.load("en_core_web_sm")
```

If the model is ever missing, this shells out to a bare `python` (which may not
be the venv interpreter) and downloads with **no timeout and `capture_output`**,
so it can block forever with the output invisible. `en_core_web_sm` 3.8.0 *is*
installed here, so this path is not currently taken — but on a fresh clone
without the model, the first `BaseEngine()` in the suite is an unbounded network
call. That is the one place the suite could still genuinely hang; the runner's
`timeout` is what makes it survivable.

---

## 3 · Hazards a developer must know

### 3.1 The real database (839 MB) — **now protected, verify before you rely on it**

`backend/ebook_reader.db` is **839 905 280 bytes** of a real library.

`main.py`'s lifespan calls `create_engine_and_tables()` with no argument, which
resolves to that file and runs `_migrate()` against it (`PRAGMA` + `ALTER TABLE`).
Any test that entered the lifespan therefore opened and migrated the developer's
actual library. This was not theoretical: running `test_voices_router.py` and
friends during this investigation moved the file's mtime, and the same runs left
`backend/voices/custom1.pt` and `backend/voices/test_voice.pt` behind (since
deleted).

`tests/conftest.py` now installs an **autouse** guard that, for every test:

* points `db.database.engine` at a fresh in-memory engine,
* rewrites `db.database._DEFAULT_DB` into `tmp_path`,
* replaces `main._init_kokoro` / `main.create_engine_and_tables` once `main` is
  imported,
* redirects `uploads/`, `voices/` and `exports/` under `tmp_path`, and
* `chdir`s into a scratch directory at import time.

**Verified:** a full-suite run leaves the `ebook_reader.db` mtime byte-identical
(before `11:50:41.238470479`, after `11:50:41.238470479`).

**The guard only applies inside pytest.** A scratch script that does
`from main import app` + `with TestClient(app)` still hits the real database and
loads the real model. If you write one, point `DB_PATH` at a temp file first.

### 3.2 The corpus in `~/Documents/EBooks` is a hard dependency

`test_pdf_engine.py` and `test_epub_ocr_engines.py` read
`~/Documents/EBooks/cleancodebook.pdf` (3.7 MB) and `cleancodebook.epub` (2.8 MB).
They are read-only, but they **fail on a machine without those books** — the
files are not in the repo and `conftest.py`'s `EBOOKS_DIR` fixtures just build
paths. There is no skip guard, so a fresh clone sees two red files.

### 3.3 Tests write to the working directory

The routers resolve `uploads/`, `voices/` and `exports/` relative to the process
CWD. Under pytest the conftest `chdir`s to a sandbox, so nothing lands in the
repo. Run a router by hand, or run pytest with the guard defeated, and those
directories get created in whatever directory you are standing in.

### 3.4 `BaseEngine()` is not free

`test_base_engine.py` and `test_text_engine.py` are unit tests in spirit, but the
fixture builds `BaseEngine()` **per test**, and that calls
`spacy.load("en_core_web_sm")` every time — ~0.5–2 s each. Together they are
~22 s of the fast subset for 17 tests. They are kept in `fast` because they are
reliable, but they are the reason `fast` is ~45 s rather than ~20 s.

### 3.5 `test_system_capabilities.py` pokes `main` internals

It monkeypatches module globals such as `main._init_remote_kokoro`. While
`main.py` is being edited this is the first file to go red with
`AttributeError: module 'main' has no attribute ...`. That is a real regression
signal, not suite noise — but it does mean this file is unusually coupled to the
current shape of `main.py`.

---

## 4 · Known-failing baseline

Before blaming your change, check whether the failure is in a file this list
already covers, and whether the file is currently being edited.

As measured on **2026-09-26 ~12:34** (whole suite, `./scripts/test.sh backend`,
`6 failed, 553 passed, 1 xfailed in 100.4 s`):

| Test | Failure | Whose problem |
|---|---|---|
| `test_system_capabilities.py::TestBackendSelection::test_local_is_the_default_and_never_touches_the_remote` | `AttributeError: module 'main' has no attribute '_init_remote_kokoro'` | in-flight edit of `backend/main.py` — the test monkeypatches a global that has been renamed |
| `…::test_remote_failure_falls_back_to_local_and_records_the_reason` | same | same |
| `…::test_remote_success_uses_the_remote_backend` | same | same |
| `…::test_total_failure_is_recorded_not_silent` | same | same |
| `test_mp3_export.py::TestListExports::test_list_includes_book_title_and_status` | `IndexError: tuple index out of range` | in-flight `routers/mp3.py` refactor |
| `test_mp3_export_nonblocking.py::test_export_is_no_longer_a_coroutine_without_await_points` | `assert 'run_in_threadpool' in ...` failed | same refactor, mid-edit |

Those same four `test_system_capabilities` failures are what `./scripts/test.sh fast`
reports today (`4 failed, 530 passed, 1 xfailed in 35.1 s`). If you see exactly
this set, nothing you did caused it.

**The baseline moves within minutes.** During this investigation the same
command reported, at different times: 0 failures; 4 failures; 27 failures; 49
failures. Every swing tracked a file some other agent was writing at that
moment (`conftest.py`, `routers/tts.py`, `services/tts_engine.py`,
`frontend/src/tests/stores/settings.test.ts`). Re-run `./scripts/test.sh fast`
and compare against the runner's failure list rather than against this table.

Two historical facts worth keeping, because they explain old reports:

* `test_tts_engine.py` had **13 pre-existing failures** and `test_speed_engine.py`
  was intentionally red (TDD). Both are green now (38 and 60 tests).
* Under the old `conftest.py`, `test_base_engine.py` passed 9/9 alone but errored
  in a full run, because tests assigned `db.database.engine` directly and only
  some restored it. The autouse guard fixes the leak.

---

## 5 · Running selectively

```bash
./scripts/test.sh --help                 # all modes
./scripts/test.sh fast                   # 27 files, slow pair excluded, ~45 s
./scripts/test.sh slow                   # only the excluded pair, ~115 s
./scripts/test.sh backend test_tts_engine
./scripts/test.sh backend tests/test_folders.py
./scripts/test.sh backend -k "cache and speed"
./scripts/test.sh backend                # the whole backend suite
./scripts/test.sh changed                # tests mapped from the diff vs origin/main
CHANGED_BASE=HEAD ./scripts/test.sh changed   # ...or just your uncommitted work
```

Why `fast` excludes exactly those files, and what it therefore does **not**
promise:

* `fast` = every `tests/test_*.py` **except** the two names in `SLOW_TESTS` at
  the top of `scripts/test.sh`. It is a *blocklist*, so a newly added test file
  is included automatically — you do not have to remember to register it.
* It does **not** run `test_pdf_engine.py` or `test_epub_ocr_engines.py`.
  If you touched `services/pdf_engine.py`, `services/epub_engine.py`,
  `services/ocr_engine.py` or `services/base_engine.py`, a green `fast` says
  nothing about you: run `./scripts/test.sh slow` too.
* `changed` is a **heuristic**, not a dependency analysis. It maps a changed
  module to `tests/test_<module>.py`, or to a textual search capped at 3 matches,
  and gives up (telling you so) when a module is referenced too widely. It cannot
  see indirect coverage. Use it to iterate, not to conclude.

pytest's own config lives in `backend/pytest.ini` (it takes precedence over
`backend/pyproject.toml`, which is owned by the uv migration). It sets
`asyncio_mode = auto`, `-q --tb=short`, disables `cacheprovider` (so `--lf`/`--ff`
are unavailable by design) and registers a `slow`/`db`/`integration`/`unit`/
`realbook`/`network` marker scheme. **No test currently carries those markers** —
the test bodies are owned by another change, so selection is done with the file
list in `scripts/test.sh` instead of `-m`.

---

## 6 · Measured results

All on 2026-09-26, `backend/.venv/bin/python` (Python 3.12.3, the uv-managed env
— the runner auto-detects it), under heavy CPU contention from other agents.

| Command | Wall clock | Result |
|---|---:|---|
| `./scripts/test.sh fast` | **40–43 s** | every file except the two slow ones (28 at the time of writing), 540 passed, 1 xfailed, 4 failed¹ |
| `./scripts/test.sh fast` (on a quieter tree, ~11:55) | **45.8 s** | 27 files, 534 passed, 1 xfailed, 0 failed |
| `./scripts/test.sh slow` | **86 s** | 2 files, all passed |
| `./scripts/test.sh backend` (whole suite) | **105 s** | 560 tests, 553 passed, 1 xfailed, 6 failed¹ |
| `./scripts/test.sh backend test_text_filter` | **4.5 s** | 42 passed |
| `./scripts/test.sh backend test_tts_engine` | **5.2 s** | 38 passed |
| `./scripts/test.sh unit` (vitest) | **5 s** | 15 files, 212 passed |
| `./scripts/test.sh unit <file>` | 2.6 s | one file |
| `npm run test:unit:changed` | 3.3 s | 8 files, 139 passed |
| `./scripts/test.sh check` (svelte-check) | **5.6 s** | clean |
| `./scripts/test.sh e2e` | not run | e2e execution is paused by decision — see §7 |

¹ The failures are the six listed in §4; they belong to other agents' in-flight
edits, not to the runner or to this config. `fast` reports the four
`test_system_capabilities` ones.

**What "before" looked like:** there was no way to run a subset at all. The only
option was the whole suite, measured at
`28 failed, 188 passed, 1 skipped, 27 errors in ~58 s`, with a re-run that had to
be SIGTERM-killed at 600 s. `fast` is therefore ~40 s *and* bounded, and it is
now possible to check a change without dragging in a real book parser.

Note that `fast` (40 s) + `slow` (86 s) is *more* than the whole suite (105 s):
running everything in one process amortises interpreter start-up and the spaCy
model. The point of splitting is selectivity, not total throughput.

### 6.1 pytest-xdist: not used, and not measured as faster

`pytest-xdist` is **not installed** in either environment, and `./scripts/test.sh`
does not depend on it — so there is no before/after number to report, and none is
claimed. Beyond availability, it is a poor fit here: `conftest.py` sandboxes the
working directory at import time and monkeypatches process-wide globals
(`db.database.engine`), so worker processes would each get their own sandbox
while sharing the ASGI app and module globals per worker — the classic recipe for
flaky, order-dependent failures in exactly the suite that just stopped being
order-dependent. Measured `fast` is 40–46 s single-process. If you want to try
it, do it as an experiment and measure both the time and the flake rate before
adopting it; do not assume it helps.

---

## 7 · Frontend

`frontend/package.json` already carries these (no new dependency was added):

```bash
npm run test:unit                                  # all vitest, 15 files / 212 tests, ~5 s
npm run test:unit -- src/tests/stores/settings.test.ts   # one file (npm's `--` passes args)
npm run test:unit:changed                          # vitest --changed, ~3 s
npm run test:e2e                                   # Playwright: the @critical flows only
npm run test:e2e:all                               # Playwright: all 121 specs (E2E_ALL=1)
npm run test:e2e:list                              # list specs/tests without running them
npm run check                                      # svelte-check
```

**e2e policy (changed after this work started).** Only *critical* user flows get
Playwright coverage. `playwright.config.ts` sets a default `grep` of `/@critical/`,
so `npm run test:e2e` runs the 32 tagged tests and `E2E_ALL=1` (or
`npm run test:e2e:all`) runs all 121. `frontend/tests/README.md` lists them and
explains the reasoning. `./scripts/test.sh e2e` therefore just forwards to
`npm run test:e2e` — it never widens the selection on its own, and it can run a
single spec by name (`./scripts/test.sh e2e library`).

**Execution is deliberately paused** — the human ruling was no e2e runs until
implementation is finished, so nothing here has been verified by running
Playwright. `gates` still *defines* the e2e step (it is one of the four handoff
gates) but it was not executed either.

Two things to know before you do run it:

1. **The browser build is missing.** `@playwright/test` 1.59.1 wants
   `chromium_headless_shell-1217`; `~/.cache/ms-playwright` only holds `-1234`
   and `-1243`, so every spec fails instantly with
   `browserType.launch: Executable doesn't exist at .../chromium_headless_shell-1217/...`.
   `npx playwright install chromium` was killed at 420 s here having downloaded
   nothing, even though the CDN is reachable (a ranged `curl` against the exact
   `chrome-headless-shell` URL returned HTTP 206 at ~3.2 MB/s), so this
   environment's network path, not the CDN, is the likely obstacle.
2. **e2e needs the API.** The specs assert against a live backend on `:8000`
   (`cd backend && uv run uvicorn main:app`); Playwright starts only the Vite dev
   server itself.

### 7.1 Do not add `-q` to a pytest invocation

`backend/pytest.ini` already sets `addopts = -q --tb=short`. pytest merges CLI
flags with `addopts`, so **`pytest -q` becomes `-qq` and the `N passed` summary
line disappears entirely** — a fully green run prints nothing you can grep for.
This bit two separate measurements during this work. `scripts/test.sh` passes
neither `-q` nor `--tb`, deliberately; the comment above its `PYTEST` array says
so. If you script pytest yourself, do not add `-q`.

Relatedly: `scripts/test.sh` writes its per-suite logs to
`test-results/test-sh/` (already gitignored) rather than `/tmp`, because under a
sandboxed agent harness each shell call may get its own private `/tmp`, making a
printed `/tmp` path unreadable from the next call.
