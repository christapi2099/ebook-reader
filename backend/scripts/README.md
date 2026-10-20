# backend/scripts: what each script does and how to rerun it

Run from `backend/`. Main env: `uv run python scripts/<name>.py`. AMD GPU env: call
`.venv-rocm/bin/python` directly with `HSA_OVERRIDE_GFX_VERSION=10.3.0` (never `uv run`; it can swap in CUDA torch).
All of these leave the app database and the renders directory untouched unless noted.

| Script | Purpose | Example | Output / cost |
|---|---|---|---|
| `render_baseline.py` | Golden compare of a full real render (synth, CTC align, tiers, manifest). Engines: `piper`, `kokoro-cpu`, `kokoro-onnx`, `kokoro-gpu`, `*-replay`, `fake`. | `render_baseline.py compare --engine kokoro-gpu --golden .baseline/kokoro-cpu.json` | exit 0 = within tolerance. ~40 s. Goldens in `.baseline/` (not in git). |
| `bench_gpu_synth.py` | Kokoro speed on real book sentences with 1..N parallel processes. | `HSA_OVERRIDE_GFX_VERSION=10.3.0 .venv-rocm/bin/python scripts/bench_gpu_synth.py --book-prefix 6f2c --procs 1,2,4 --out ../docs/research/gpu-synth-bench.json` | speed_x per process count. ~3 min. Read-only DB. |
| `bench_modal.py` | Time the deployed Modal Kokoro app (cold/warm, chapter in K requests). | `scripts/bench_modal.py book.pdf --parallel 1,4,8` | Spends real GPU money (cents). |
| `verify_render.py` | Check a published render on disk. | see `--help` | read-only |
| `atempo_tiers.py`, `precompile_sample.py`, `spike_chapter.py` | Older spikes. | | |

## Measured results (record each run here with the date)

- 2026-10-20, RX 6700 XT, MIOpen off, 80 sentences/process, while a tier encode ran on the CPU:
  1 proc 24.0x, 2 procs 37.9x, 4 procs 45.1x real time (median sentence 0.47 s at 4 procs).
  The 8-worker CPU ONNX render of the 28.8 h Deep Learning book ran at about 15x.

## Testing a full GPU render in the app

Do not do this while another book is rendering: switching the engine restarts the running render.
1. `GPU_ENV=rocm ./start.sh` (or the exported-env uvicorn command in `docs/AMD_ROCM.md`).
2. Add a short book (a few chapters), wait for it to finish on the current engine, note `processing.json`
   `phase_timings` in `backend/renders/<book>/`.
3. Switch the engine to GPU: `curl -XPOST localhost:8000/api/system/engine -H 'content-type: application/json' -d '{"engine":"gpu"}'`
   (check the body shape in `routers/system.py`), then `POST /library/<id>/reprocess`.
4. Compare the new `phase_timings` and listen to the result.
