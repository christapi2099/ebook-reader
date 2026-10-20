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
| `slim_manifest_check.py` | Sizes and parse times of a render's manifest and of its slim (schema 2) form, plus an exhaustive check that every tier/segment/word of the slim form equals the published file; `--url` also fetches what the backend serves and compares it; `--node` times `JSON.parse` in node. | `slim_manifest_check.py --book <id> --url http://127.0.0.1:8000 --node` | exit 0 = equal everywhere. Read-only (the published manifest is only read). ~25 s and ~1.8 GB peak RAM for a 358 MB schema 1 manifest. |
| `bench_tier_encode.py` | Time the tier encode (one ffmpeg per tier vs the chunked pool) and the drift audit on a published render; `--compare` checks the re-encode has the published files' packet count, granule, duration and decoded length. | `bench_tier_encode.py renders/<book>/<gen> --workers 1,12 --compare` | Render dir read-only; temp output removed. ~30 s for a 13 min book. |
| `atempo_tiers.py`, `precompile_sample.py`, `spike_chapter.py` | Older spikes. | | |

## Measured results (record each run here with the date)

- 2026-10-20, RX 6700 XT, MIOpen off, 80 sentences/process, while a tier encode ran on the CPU:
  1 proc 24.0x, 2 procs 37.9x, 4 procs 45.1x real time (median sentence 0.47 s at 4 procs).
  6 procs 41.1x (slower than 4), 8 procs: 2 of 8 workers died with CUDA out of memory (12 GB card; peak VRAM sampled 12.9 GB).
  The 8-worker CPU ONNX render of the 28.8 h Deep Learning book ran at about 15x.

- 2026-10-20, tier encode + drift audit on the 13.3 min render `40c5b733.../1` (CPU, another render running):
  old pipeline (1.0 + 7 tiers, 4 ffmpeg at once, marker audit that re-encodes every tier) 16.4 s encode + 19.0 s audit;
  new tier set (1.0, 1.5, 2.0, 2.5, 3.0) one ffmpeg per tier 10.1 s + 0.13 s audit; chunked pool 8 workers 6.8 s,
  12 workers 5.5 s, 15 workers 4.8 s (+0.12 s audit). Chunked output: same packet count, final granule, ffprobe duration
  and decoded length as the published files, SNR vs the encoder input within 0.01 dB, 0 forced (non-silent) splits.

- 2026-10-20, slim manifest of the 28.8 h Deep Learning book `6f2c810d.../1` (schema 1, 8 tiers incl. 0.75/1.25/1.75):
  schema 1 file 357.8 MB (the old endpoint re-serialized it: 334.8 MB, 40.9 s to first byte); slim 38.4 MB,
  6.1 MB gzipped; 8 tiers x 18,539 segments x 246,201 words checked, max |err| 0 ms (the division is bit-identical).
  node `JSON.parse`: schema 1 ~1.7 s and ~360 MB heap, slim ~170 ms and ~38 MB.

## Testing a full GPU render in the app

Do not do this while another book is rendering: switching the engine restarts the running render.
1. `GPU_ENV=rocm ./start.sh` (or the exported-env uvicorn command in `docs/AMD_ROCM.md`).
2. Add a short book (a few chapters), wait for it to finish on the current engine, note `processing.json`
   `phase_timings` in `backend/renders/<book>/`.
3. Switch the engine to GPU: `curl -XPOST localhost:8000/api/system/engine -H 'content-type: application/json' -d '{"engine":"gpu"}'`
   (verified in `routers/system.py`), **restart the backend** (the choice is saved, not swapped live), then
   `POST /library/<id>/reprocess`.
4. Compare the new `phase_timings` and listen to the result.
