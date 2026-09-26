# Kokoro runtime picks: CPU, local GPU, Modal cloud GPU

Research date: 2026-09-25/26. Scope: which Kokoro runtime each processing mode of this app should
use, judged against what the backend depends on — word timestamps (`KPipeline` tokens
`start_ts`/`end_ts`) for highlighting, the native `speed` param, user-uploaded `.pt` voices, and
.deb/Flatpak install size. Complements `kokoro-accelerated-backends-benchmarks.md` (cited below as
"backends doc"), which holds the verified third-party numbers.

## Verdict

| Mode | Pick | Deciding reason |
|---|---|---|
| CPU | ONNX Runtime + `onnx-community/Kokoro-82M-v1.0-ONNX-timestamped` (fp32), fed by `misaki` G2P | Reproduces KPipeline word timings exactly; ~0.5 GB env vs 6.8 GB |
| Local GPU | Official `kokoro` on PyTorch — CUDA (NVIDIA), ROCm with MIOpen disabled (AMD) | Fastest option that keeps timestamps; ONNX/TRT/compile don't pay off |
| Modal | Same PyTorch code, small GPU, scale-to-zero, SDK calls | Cost is dominated by idle time, not compute |

The Kokoro-82M v1.0 checkpoint is the current model for all three; nothing newer has been released
(hexgrad's Hugging Face models: v1.0 updated 2025-04-10, v1.1-zh 2025-03-04 for Chinese only).

## 1. Local measurements (i7-11850H, 16 threads; NVIDIA T600 Laptop 4 GB)

8 prose sentences (~45 s of audio), `af_heart`, speed 1.0, one warm-up call first.

| Runtime | Conditions | Throughput | Per sentence (median / max) |
|---|---|---|---|
| `kokoro` 0.9.4 PyTorch CPU | default threads, load ~8 | 2.6x realtime | 2.2 s / 3.8 s |
| `kokoro` 0.9.4 PyTorch CUDA (torch 2.14+cu130) | T600 | **18.1x realtime** | **0.32 s / 0.52 s** |
| `kokoro-onnx` 0.6.1, `kokoro-v1.0.onnx` fp32 | default threads, load ~8 | 1.8x | 2.7 s / 5.9 s |
| `kokoro-onnx` 0.6.1, `kokoro-v1.0.int8.onnx` | default threads, load ~8 | **0.5x** (slower than fp32) | 10.6 s / 16.4 s |
| PyTorch CPU (bracket start) | 4 threads, load ~38 | 0.2x | 26.5 s |
| ORT fp32 release model | 4 threads, load ~38 | 0.7x | 8.8 s |
| ORT fp32 timestamped model | 4 threads, load ~38 | 0.7x | 9.2 s |
| ORT fp16 timestamped model | 4 threads, load ~38 | 0.6x | 9.0 s |
| PyTorch CPU (bracket end) | 4 threads, load ~38 | 0.3x | 21.6 s |

CPU runs shared the machine with other benchmark/test jobs, so absolute CPU numbers are
pessimistic. The ranking flips with load (PyTorch ahead at load ~8, ORT ~3x ahead at load ~38):
**treat CPU speed as a wash**; ONNX's decisive CPU advantage is footprint (ORT env 186 MB, 0.5 GB with
spaCy + misaki; the current PyTorch venv is 6.8 GB). Re-run on an idle machine before quoting.

Correctness checks:

- **Timestamp parity.** KPipeline phonemes fed to the timestamped ONNX model: identical token
  count, `durations` within 0.5 frame of KPipeline's rounded `pred_dur`, identical sample counts on
  4/4 sentences. Rounding the ONNX durations therefore reproduces KPipeline's word timings.
- **`.pt` voices without torch.** A ~30-line restricted unpickler over the `torch.save` zip reads
  `af_heart.pt` to a `(510, 1, 256)` float32 array bit-identical to `torch.load`, the same layout
  as the voices in `voices-v1.0.bin`.
- **misaki without torch.** `misaki[en]` depends on `spacy-curated-transformers`, which imports
  torch. Installing `misaki` + `spacy` + `num2words` + `phonemizer-fork` + `espeakng-loader`
  (no `[en]` extra) gives a torch-free G2P with output identical to the backend's, including the
  espeak fallback for unknown words.
- **espeak-ng path-length trap.** espeak-ng silently ignores its data directory when the path is
  too long (failed at 182 chars, worked at 110) and falls back to the build machine's path, failing
  with `/home/runner/work/espeakng-loader/.../phontab: No such file or directory`. Keep the data
  directory on a short path in packaged builds.
- `phonemizer` (pulled by `kokoro-onnx`) and `phonemizer-fork` (pulled by `misaki`) install the
  same module and must not be installed together.

## 2. CPU mode

Pick: ONNX Runtime on `onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/onnx/model.onnx` (inputs
`input_ids`, `style`, `speed`; outputs `waveform`, `durations`), driven by a thin adapter that uses
misaki phonemes and the existing timestamp code.

Rejected:

- `kokoro-onnx` package as-is — its timing support checks for an output named `duration`; the
  onnx-community export names it `durations`, and the package's own release models have neither. It
  also phonemizes with espeak, not misaki (different pronunciation and word splits).
- int8/q8/uint8 exports — 0.5x locally; independently 9.92 s vs 0.48 s fp32 (RTX 3060 Ti box) and
  27.4 s vs 9.49 s fp32 (Core Ultra 7 258V) in kokoro-onnx #112 (backends doc rows 47, 49).
- sherpa-onnx — no word timestamps for TTS output (k2-fsa/sherpa-onnx #3705, open).
- TTS.cpp — Kokoro is CPU-only (no CUDA/Vulkan), repo last pushed 2025-10-05.
- Kokoros (Rust) — has a word-timestamp TSV sidecar and a CUDA feature, but is a separate service.
- OpenVINO — the 3–5x figures in hexgrad/kokoro PR #362 are unpublished downstream claims.

## 3. Local GPU mode

Pick: official `kokoro` on PyTorch.

- ONNX Runtime CUDA EP is not worth it at 82M parameters: reports span 3.3x faster than CPU
  (RTX 4090) to ~2% (RTX 3060 Ti) to slower than CPU (A100, 39 Memcpy nodes); an AWS benchmark
  found ORT CUDA 2–3x slower than PyTorch on A10G/L4/T4.
- TensorRT: no published working Kokoro engine; exports fail on dynamic shapes (backends doc §2).
- `torch.compile`: 1.34x best case (hexgrad/kokoro PR #91), still failing in other configs
  (pytorch#149570).

AMD:

- PyTorch ROCm works, but MIOpen compiles a kernel per unseen tensor shape and Kokoro's decoder
  shape changes with every text. On an RX 7900 XTX (ROCm 7.2): 2433 ms per new text with MIOpen,
  268 ms without (remsky/Kokoro-FastAPI PR #518, shipped in v0.8.2). Fix:
  `torch.backends.cudnn.enabled = False` when `torch.version.hip` is set.
- ROCm 7 officially targets RDNA3/RDNA4 consumer cards (RX 7000 / 9000); RX 6000 support is thin and
  APUs vary. Unsupported cards should fall back to CPU mode — the vendor-neutral GPU path (WebGPU via
  `kokoro-js`) exposes no word-timestamp API.

Intel Arc: native `torch.xpu` is an open, unmerged PR (hexgrad/kokoro #362, 4.4x over CPU).

Packaging: CUDA/ROCm PyTorch is multiple GB, so GPU mode should be an optional download rather than
part of the base package.

## 4. Modal mode

Prices from modal.com/pricing (fetched 2026-09-25), per hour: T4 $0.590, L4 $0.799, A10 $1.102,
L40S $1.951, A100-40GB $2.099, H100 $3.949. Per-second billing; $30/month free on Starter.

| GPU | Kokoro PyTorch throughput (AWS gist) | 9-hour book (32,400 s audio) |
|---|---|---|
| T4 | 36x realtime | 900 s → ~$0.18 incl. CPU/mem |
| L4 | 81x | 400 s → ~$0.10 |
| A10 | 96x | 338 s → ~$0.12 |

- Compute is ~$0.01–0.02 per hour of audio. **Idle time dominates**: an L4 kept warm for an hour
  costs ~$0.80 regardless of output; `min_containers=1` on an L4 is ~$575/month.
- Consequence for the GPU choice: T4 has the lowest idle price, so it is the right default for
  interactive reading (idle-dominated); L4 has the lowest cost per synthesized hour, so it is the
  right pick for bulk export (compute-dominated).
- CPU-only Modal containers are ~$0.20 per audio-hour — 15–20x the GPU cost.
- `KModel.forward_with_tokens` handles batch size 1 only, so `@modal.batched` does not help; send
  bigger inputs per call (a chapter) instead.
- Cold start: ~10–20 s without snapshots (estimate). CPU memory snapshots are GA; GPU memory
  snapshots are alpha (`experimental_options={"enable_gpu_snapshot": True}`) — keep a
  non-snapshot path working.
- Use the Python SDK (`modal.Function.from_name(...).remote/.spawn/.map`), not web endpoints: no
  150 s HTTP limit, auth via the user's Modal token, and a public GPU endpoint is a standing cost
  risk.

Recommended use pattern:

- Interactive: `min_containers=0`, `scaledown_window≈60`, `@modal.concurrent(max_inputs=4)` with a
  lock around the forward pass; fire a warm-up `.spawn()` when a book opens; synthesize the current
  sentence first, then prefetch the rest of the chapter in one call into the local SQLite cache and
  let the container scale down (~$0.05 per audio-hour).
- Export: split by chapter and `.map()` over 4–8 containers, `scaledown_window` 2–10 s
  (~$0.10–0.15 per 9-hour book).
- Custom `.pt` voices: send the tensor bytes (~0.5 MB) with a SHA-256 key and cache per container.
- Precedent: Modal's own Kokoro service (modal-projects/open-source-av-ragbot) uses L40S + GPU
  snapshots + `max_inputs=10`, sized for a multi-user voice bot.

## 5. Kokoro versions and forks (checked 2026-09-25/26)

| Project | Version / last activity | Backend | Word timestamps | `.pt` voices | Licence | Verdict |
|---|---|---|---|---|---|---|
| Kokoro-82M v1.0 (HF) | updated 2025-04-10, 11.7M downloads | — | — | — | Apache-2.0 | Current model |
| Kokoro-82M v1.1-zh | 2025-03-04 | — | — | — | Apache-2.0 | Chinese only |
| v0.19 (`hexgrad/kLegacy`) | 2025-01-26 | — | — | — | — | Legacy |
| `kokoro` (pip) | 0.9.4, 2025-04-05; repo pushed 2025-08-06 | PyTorch CPU/CUDA/ROCm/MPS | Yes | Yes | Apache-2.0 | Reference; quiet but stable |
| `misaki` | 0.9.4, 2025-04-05 | G2P | — | — | Apache-2.0 | Keep |
| onnx-community `-ONNX-timestamped` | 2025-02-21 | ONNX: fp32 326 MB, fp16 163, q8f16 86, quantized 92, q4, q4f16, uint8, uint8f16 | Yes (`durations`) | Via conversion | Apache-2.0 | CPU pick |
| onnx-community `-ONNX` | — | Same variants | No | Via conversion | Apache-2.0 | Superseded by timestamped |
| thewh1teagle `kokoro-onnx` | 0.6.1, 2026-08-19 | ORT + espeak | Only for an output named `duration` | Conversion | MIT | Mismatched with onnx-community export |
| remsky Kokoro-FastAPI | v0.9.0, 2026-09-10 (very active) | PyTorch; CPU / NVIDIA / ROCm images | Yes (`/dev/captioned_speech`) | Yes; voice-clone tuner | Apache-2.0 | Best maintained server; ROCm fixes worth copying |
| `kokoro-js` | 1.2.1, 2025-05-03 | Transformers.js WebGPU/WASM | No API | — | Apache-2.0 | Browser demos |
| sherpa-onnx | v1.13.8, 2026-09-10 | ONNX, many platforms | No (#3705) | Conversion | Apache-2.0 | Not for highlighting |
| Kokoros (Rust) | pushed 2026-08-04 | ONNX, optional CUDA | TSV sidecar | Conversion | none declared | Separate service |
| TTS.cpp | pushed 2025-10-05 | GGUF, Kokoro CPU-only | — | — | MIT | Stale |
| mlx-audio | v0.5.6, 2026-09-24 | Apple MLX | — | — | MIT | macOS only |
| CoreML ports (FluidInference, laishere, mattmireles) | 2025–26 | Apple ANE | — | — | — | macOS/iOS only, 14–27x realtime |
| Batched forks (nimbleEdge/kokoro, wwang1110/kokoro_batch) | — | PyTorch | — | — | — | No batch-vs-single multiplier published |

## 6. Consensus and disagreement

Agree: v1.0 is the model; PyTorch is fastest on GPU; int8 is slow on x86 CPUs; ORT/TRT don't pay
on GPU for 82M params; AMD works via ROCm once MIOpen per-shape compilation is avoided; only official
`kokoro`, Kokoro-FastAPI, and the timestamped ONNX export give real word timestamps among
Linux-desktop-viable options.

Disagree: CPU PyTorch vs ORT speed (load-dependent locally; third-party 5.9x realtime on an
i7-13700KF vs 3.5 s first audio on an older i7); ORT CUDA EP (0.5x–3.3x); keep-and-prewarm MIOpen
(#454, gfx1151, ~2 h warm-up) vs disable it (#518, gfx1100, simpler and faster on new text).

## Sources

- Local runs this session: kokoro 0.9.4, torch 2.14.0+cu130, kokoro-onnx 0.6.1, onnxruntime 1.30.0, misaki 0.9.4
- `docs/research/kokoro-accelerated-backends-benchmarks.md`
- https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped
- https://github.com/thewh1teagle/kokoro-onnx/issues/112 · https://github.com/thewh1teagle/kokoro-onnx/issues/37
- https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653
- https://github.com/remsky/Kokoro-FastAPI (README, releases v0.8.0–v0.9.0) · PR #518 · issue #454
- https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/native_linux/native_linux_compatibility.html
- https://github.com/hexgrad/kokoro/pull/91 · https://github.com/hexgrad/kokoro/pull/362 · https://github.com/pytorch/pytorch/issues/149570
- https://github.com/k2-fsa/sherpa-onnx/issues/3705
- https://modal.com/pricing · https://modal.com/docs/guide/cold-start · https://modal.com/docs/guide/memory-snapshot · https://modal.com/docs/guide/concurrent-inputs · https://modal.com/docs/guide/trigger-deployed-functions
- https://github.com/modal-projects/open-source-av-ragbot/blob/main/server/tts/kokoro_tts.py
- https://github.com/lucasjinreal/Kokoros · https://github.com/mmwillet/TTS.cpp · https://github.com/Blaizzy/mlx-audio · https://www.npmjs.com/package/kokoro-js
