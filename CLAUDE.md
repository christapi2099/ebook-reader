# Kokoro Ebook Reader

Local TTS reader: FastAPI + Kokoro TTS (backend) / Svelte 5 + PDF.js (frontend).

## Run
```bash
# Backend (Python 3.12, port 8000) — uv manages backend/.venv from backend/uv.lock
cd backend && uv sync && uv run uvicorn main:app --reload

# Frontend (port 5173)
cd frontend && npm run dev
```

## Key Files
| File | Purpose |
|---|---|
| `backend/services/tts_engine.py` | Kokoro synthesis, AudioCache, SynthJob(speed=) |
| `backend/routers/tts.py` | WebSocket /ws/tts/{id} — play/seek/pause+speed |
| `frontend/src/lib/stores/audio.ts` | Web Audio API, generation counter, decode chain |
| `frontend/src/lib/stores/reader.ts` | sentences[], currentIndex, isPlaying, speed |
| `frontend/src/lib/components/PDFViewer.svelte` | PDF.js canvas + sentence overlay |
| `frontend/src/lib/api.ts` | TTSSocket + all HTTP calls |

## Hard Rules

**Backend**
- Kokoro yields 3-tuples `(graphemes, phonemes, audio_ndarray)` — use `result[-1]`, sample_rate=24000
- AudioCache key = `SHA256(text:voice:speed)` — speed is part of key
- Use `import db.database as _db` + `_db.engine` at call time (not import-time)
- Run Python through uv: `cd backend && uv run <cmd>` (env is `backend/.venv`, locked by `backend/uv.lock`). Re-run `uv sync` after pulling; add deps with `uv add` / `uv add --dev` (never hand-edit `requirements.txt` — it is generated from the lock)

**Frontend — Svelte 5 (compiler-enforced, violations = build errors)**
- `$props()` not `export let` · `$state()` not reactive `let` · `$derived()` not `$:`
- `onclick` not `on:click` · callback props (`onEventName`) not `createEventDispatcher`
- No `@apply` in Tailwind v4
- All API calls → `src/lib/api.ts` only, never `fetch()` in components

**Coordinates**
- PyMuPDF + PDF.js canvas share top-left origin. Direct mapping: `x = fitz_x * 1.5`, `y = fitz_y * 1.5`. No y-flip.

**Audio**
- Speed handled by Kokoro native `speed` param — browser `playbackRate` stays at `1.0`
- pdfjs-dist v5: `import * as pdfjsLib` + `import workerUrl from '...?url'` at **module level** (not inside function)

## Dispatch (OpenCode)
```bash
~/.opencode/bin/opencode run -m <model> "$(cat .opencode/instructions.md)\n\n<task>"
```
Models: `deepseek/deepseek-reasoner` (plan) · `deepseek/deepseek-chat` (fast) · `openrouter/qwen/qwen-2.5-coder-32b-instruct` (impl) · `openrouter/x-ai/grok-3-mini-beta` (review)

Always prepend `$(cat .opencode/instructions.md)` to agent prompts for Svelte rules + project context.

## Working with other agents (read this before any git command)

Several agents may be editing this repo at once. **Never run `git stash`,
`git checkout -- <path>`, `git restore`, `git reset` or `git clean` in the shared
working tree.** Each of those can silently destroy another agent's uncommitted
work, and it has already happened here once: a `git stash push` of a file two
agents co-owned reverted it to HEAD and lost both agents' edits.

**Prefer a worktree.** Each agent gets its own checkout with its own index and
HEAD, so its git commands cannot touch anyone else's files:

```bash
scripts/agent-worktree.sh add <name>     # creates .worktrees/<name> on agent/<name>
scripts/agent-worktree.sh list
scripts/agent-worktree.sh remove <name>
```

It symlinks the heavy gitignored state (`backend/venv`, `backend/.venv`,
`.uv-cache`, `frontend/node_modules`) instead of copying gigabytes. File isolation
is real, but **`refs/stash` is shared across worktrees** — a stash made in one
appears in `git stash list` everywhere, so never pop a stash you did not create.
Commit rather than stash.

If you must work in the shared tree, the safety net makes destruction recoverable:

```bash
scripts/agent-safety.sh snapshot   # or `watch 90` to run it on a loop
scripts/agent-safety.sh check      # stash list, destructive reflog, at-risk count
scripts/agent-safety.sh restore <id>
```

It captures tracked changes (a `git stash create` commit under `refs/autosave/`)
and **untracked** files (an rsync copy in `.snapshots/`), which is where most new
work lives. **Commit often** — a stash can only lose what is uncommitted, so
frequent commits are the cheapest protection available.

## Memory Bank
After significant features/bug fixes, update:
- `~/.claude/projects/-home-christapia/memory/bugs_fixed_ebook_reader.md` — bugs + fixes
- `~/.claude/projects/-home-christapia/memory/project_ebook_reader.md` — architecture + completion status
