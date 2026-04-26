# Kokoro Ebook Reader

Local TTS reader: FastAPI + Kokoro TTS (backend) / Svelte 5 + PDF.js (frontend).

## Run
```bash
# Backend (Python 3.11, port 8000)
cd backend && source venv/bin/activate && uvicorn main:app --reload

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
- `source backend/venv/bin/activate` before running Python

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

## Memory Bank
After significant features/bug fixes, update:
- `~/.claude/projects/-home-christapia/memory/bugs_fixed_ebook_reader.md` — bugs + fixes
- `~/.claude/projects/-home-christapia/memory/project_ebook_reader.md` — architecture + completion status
