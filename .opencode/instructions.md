# Kokoro Ebook Reader — OpenCode Session Instructions

## Execution Rules (MANDATORY)
- **Do NOT enter plan mode.** Write or edit files directly.
- **Do NOT ask clarifying questions.** Use the information given and proceed.
- Read required files, then immediately act. No summaries before acting.

## Stack
- **Backend**: Python 3.11, FastAPI, SQLite (SQLModel), Kokoro TTS, PyMuPDF
- **Frontend**: Svelte 5 (runes MANDATORY), SvelteKit, Tailwind CSS v4, TypeScript strict
- **Audio**: Web Audio API (AudioContext + decodeAudioData), WebSocket TTS streaming
- **PDF**: pdfjs-dist v5, static import + `?url` worker at module level

## Svelte 5 Rules (COMPILER-ENFORCED — violations are build errors)
- `$props()` not `export let`
- `$state()` not reactive `let`
- `$derived()` not `$:`
- `$effect()` not `afterUpdate`
- `onclick` not `on:click`
- Callback props (`onEventName`) not `createEventDispatcher`
- No `@apply` in Tailwind v4

## Project Conventions
- All API calls → `src/lib/api.ts` (never `fetch()` in components)
- Components → `src/lib/components/PascalCase.svelte`
- Stores → `src/lib/stores/camelCase.ts` using factory function pattern
- Color palette: `slate-*` neutrals, `blue-500` primary, `red-*` errors
- Icons: inline SVG with `w-4 h-4` / `w-5 h-5`
- No CSS modules, no `<style>` except `:global()` overrides

## Key Files
- `frontend/src/lib/stores/audio.ts` — Web Audio API + TTS WebSocket, generation counter, buffer health
- `frontend/src/lib/stores/reader.ts` — book metadata, currentIndex, isPlaying, speed
- `frontend/src/lib/components/PDFViewer.svelte` — PDF.js canvas + sentence overlay, sentenceElements Map
- `frontend/src/lib/api.ts` — TTSSocket class, all HTTP endpoints
- `backend/services/tts_engine.py` — Kokoro synthesis, AudioCache, SynthJob(speed=)
- `backend/routers/tts.py` — WebSocket handler, play/seek/pause actions

## Coordinate System
PyMuPDF (fitz) and PDF.js canvas share top-left origin, y increases downward.
Mapping: `canvas_x = fitz_x * SCALE`, `canvas_y = fitz_y * SCALE` (no flip needed)

## Backend venv
`source /home/christapia/Repos/ebook-reader/backend/venv/bin/activate`

## Memory Bank — Update After Significant Work
After completing any significant feature, major bug fix, or architectural change, append a summary to:
`/home/christapia/.claude/projects/-home-christapia/memory/bugs_fixed_ebook_reader.md` (for bugs/fixes)
`/home/christapia/.claude/projects/-home-christapia/memory/project_ebook_reader.md` (for features/architecture changes)

Format: `## N. Title\n**Symptom/Feature:**\n**Root cause:**\n**Fix:**`
Also update the In Progress section of project_ebook_reader.md to Completed when done.
