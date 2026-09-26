# End-to-end tests

Playwright drives the real app in a real browser. It is the slowest and most
brittle of the four gates, so it is deliberately kept small.

## Policy: critical flows only

`npm run test:e2e` runs **only the tests tagged `@critical`** — 32 of 121. The
rest still exist and still run, but only when you ask for them:

```bash
npm run test:e2e          # the critical flows (default)
npm run test:e2e:all      # everything (E2E_ALL=1 playwright test)
npm run test:e2e:list     # list what would run, without running it
npx playwright test tests/folders.spec.ts          # one file
npx playwright test --grep "move dialog"           # one flow
```

Add `@critical` to a test's title to include it: `test('@critical does X', ...)`.

**Why.** The suite had grown to 121 tests, and most of them guard behaviour that
is already covered faster and more precisely by unit tests — bionic word
rendering (26 tests, with `src/tests/utils/bionic-reading.test.ts` alongside),
search-highlight styling (7), buffering indicators (5), and the individual bug
regressions in `text-reader-bugs.spec.ts` (11). Keeping those in the default run
bought little and cost a lot of wall-clock. Tag a test `@critical` only if its
failure means **a user cannot read their book**.

## What is critical (32 tests)

| Flow | File | Tests | What breaking it means |
|---|---|---|---|
| Library entry | `library.spec.ts` | 3 | Books don't appear, or tapping one doesn't open it |
| Read flow | `text-reader.spec.ts` | 6 | Text doesn't become a book, or doesn't play |
| Playback controls | `mediabar.spec.ts` | 4 | Play/pause, speed and rewind stop working |
| Highlight/audio sync | `highlight-sync.spec.ts` | 6 | The reading position stops tracking the audio |
| Word-level highlight | `highlight-sync.spec.ts` | 4 | Word-by-word highlighting breaks |
| Folders | `folders.spec.ts` | 8 | Filing, filing errors, or delete-loses-books |
| PDF error state | `error-states.spec.ts` | 1 | A broken PDF fails silently |

Not critical, and deliberately excluded: `bionic-reading.spec.ts`,
`buffering-states.spec.ts`, `text-reader-bugs.spec.ts`, `simple-test.spec.ts`, and
the search-styling / cross-page / responsive-overflow cases inside
`highlight-sync.spec.ts` and `text-reader.spec.ts`.

## Running the suite

Playwright starts the **frontend** itself (`webServer` in `playwright.config.ts`).
Almost every spec mocks the API with `page.route(...)`, so a backend on :8000 is
usually **not** needed. If you add a spec that talks to the real backend, you must
start it yourself — nothing in this repo does:

```bash
cd backend && venv/bin/python -m uvicorn main:app --port 8000
```

## Gotchas

- **The pinned browser build is absent, and the config works around it.**
  Playwright 1.59.1 wants chromium revision 1217; this machine has 1234 and 1243,
  and `npx playwright install chromium` cannot fetch the pinned one (killed at
  420 s having downloaded nothing). `playwright.config.ts` therefore resolves the
  newest `chromium-*` build in `~/.cache/ms-playwright` itself — checking both the
  `chrome-linux64` and `chrome-linux` layouts — and falls back to Playwright's own
  resolution when it finds none, so a machine that has the pinned build is
  unaffected. The tradeoff is stated in the config: this is not the exact browser
  Playwright was released against. Set `PLAYWRIGHT_CHROMIUM_PATH` to override.
  Verified by running a single test, which launched and passed in 10.6 s.
- **`getByRole('switch')` in `bionic-reading.spec.ts`** used to select switches by
  unnamed position (`nth(3)`). Adding, removing or reordering a switch in
  `SettingsOverlay.svelte` silently retargeted those tests, so every switch now
  carries the accessible name of its visible label and the spec selects them by
  name (`getByRole('switch', { name: 'Bionic Reading' })`). Keep it that way:
  never reintroduce an index into a switch query.
- **Pre-existing failures.** Four tests outside the critical set fail for reasons
  unrelated to recent work (`buffering-states` selector issues and a `text-reader`
  strict-mode collision between the Upload button and the Sidebar's Upload nav
  item). They were verified pre-existing by re-running with the relevant change
  reverted. They do not affect the default run.
