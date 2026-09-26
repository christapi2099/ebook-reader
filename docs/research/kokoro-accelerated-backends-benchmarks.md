# Kokoro-82M Accelerated Backend Benchmarks — Verified Findings

Research date: this session. All figures below were read from the cited primary source.
**verified** = number appears verbatim in the cited source. **verified (derived)** = arithmetic I
performed on verified raw values (computation shown). **unverified** = could not confirm from a primary source.

---

## Numbers Table

| # | Claim | Value | Source URL | Status |
|---|---|---|---|---|
| **CoreML / Apple Silicon** | | | | |
| 1 | PyTorch CPU, M4 Pro 48GB, aggregate RTFx (461.65s audio / 27.177s inf) | 16.987x | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 2 | PyTorch MPS, same host, aggregate RTFx (partial run, crashed on long strings) | 9.960x | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 3 | MLX pipeline, same host, aggregate RTFx (461.65s / 19.401s) | 23.796x | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 4 | Swift + FluidAudio CoreML, same host, aggregate RTFx (404.30s / 17.408s) | 23.225x | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 5 | Swift+CoreML peak memory (process-wide) vs PyTorch CPU peak | 1.503 GB vs 4.85 GB | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 6 | CoreML first-run compile / subsequent load time | ~15 s first / ~2 s expected | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| 7 | CoreML speedup vs PyTorch CPU on M4 Pro (23.225 / 16.987) | ~1.37x | derived from rows 1,4 | verified (derived) |
| 8 | CoreML vs MLX on M4 Pro (23.225 / 23.796) — CoreML slightly **slower** | ~0.98x | derived from rows 3,4 | verified (derived) |
| 9 | best-case single-passage RTFx, CoreML, M4 Mac Mini 24GB | 27.4x (128 tokens) | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| 10 | mean 6-passage RTFx, CoreML, M4 Mac Mini 24GB | **25.0x** | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| 11 | worst-case single passage (short "Hello there.") RTFx | 19.8x | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| 12 | max-length 510-phoneme paragraph: 28.12 s audio in 1101.1 ms | 25.5x | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| 13 | iPhone 16 Pro mean RTFx, same chain | **16.9x** | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| 14 | M1 Mac Mini 16GB, 30 s audio | 1.958 s → **14x realtime** | [mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) | verified |
| 15 | M2 Ultra 64GB, 30 s audio | **422 ms** (~70x RT) | [mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) | verified |
| 16 | 3 s audio, M1 Mini / M2 Air / M2 Ultra | 236 / 200 / 59 ms | [mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) | verified |
| 17 | 15 s audio, M1 Mini / M2 Air / M2 Ultra | 1007 / 783 / 278 ms | [mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) | verified |
| 18 | CoreML RTF (lower better), M2 Ultra, 3/7/15/30 s | 0.020 / 0.018 / 0.017 / 0.017 | [bakeoff-results-v2](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| 19 | CoreML speedup vs PyTorch MPS, M2 Ultra | 4.0x / 3.3x / 2.8x / 3.4x | [bakeoff-results-v2](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| 20 | CoreML speedup vs PyTorch CPU, M2 Ultra | 7.2x / 6.5x / 6.1x / 5.7x | [bakeoff-results-v2](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| 21 | CoreML speedup vs PyTorch MPS, M1 Mini | 3.1x / 2.0x / 2.8x / 3.4x | [bakeoff-results-v2](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| 22 | CoreML speedup vs PyTorch CPU, M1 Mini (893.9/156.8 etc.) | 5.7x / 4.4x / 6.4x / 7.3x | derived from M1 Mini table | verified (derived) |
| 23 | PyTorch MPS OOM threshold on 24 GB M2 Air | OOM at 15 s and 30 s; MPS pool caps near 27 GB | [bakeoff-results-v2](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| 24 | Mask-aware bucketing (PR #6) gain, M1 Mini | 1.24x / 1.27x / 1.39x / 1.28x / 1.35x | [mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) | verified |
| 25 | Runway Anywhere ANE bundle, M4 Max | **14.0–26.0x realtime** | [runanywhere/Kokoro-82M_ANE](https://huggingface.co/runanywhere/Kokoro-82M_ANE) | verified |
| 26 | iPhone 16 Pro (A18 Pro), Kokoro-82M CoreML RTF + peak mem | 0.08 RTF, 676 MB | [speech-swift ios-coreml.md](https://github.com/soniqo/speech-swift/blob/7690825d1d717cf071c7a4b4fd8ab683db4d5acc/docs/benchmarks/ios-coreml.md) | verified |
| 27 | Row 26 expressed as realtime multiplier (1/0.08) | ~12.5x | derived from row 26 | verified (derived) |
| **TensorRT** | | | | |
| 28 | Any published Kokoro-82M TensorRT benchmark | **NONE FOUND** | — | verified absent (see §2) |
| 29 | TRT EP session init failure (Gather_output_0 no shape) | error, no numbers | [kokoro-onnx #50](https://github.com/thewh1teagle/kokoro-onnx/issues/50) | verified |
| 30 | `trtexec` failure, TensorRT 8.6.13 on Jetson Orin (CC 8.7, 16 SMs) | Assertion: "Cannot infer squeeze dimensions from a dynamic shape!" | [HF discussion #15](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/discussions/15) | verified |
| 31 | Assertion that Kokoro is the hardest model for TRT ("Loop/If + fused source-filter") | qualitative, no Kokoro numbers | [jetson-tts PR #1](https://github.com/vieenrose/jetson-tts/pull/1) | verified |
| **torch.compile** | | | | |
| 32 | Eager per-call latency, RTX 4070 Ti SUPER | 4.20 ms | [hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91) | verified |
| 33 | Compiled per-call latency, same host | 3.13 ms | [hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91) | verified |
| 34 | torch.compile speedup (4.20/3.13) | **1.34x** | derived from rows 32,33 | verified (derived) |
| 35 | Graph breaks remaining after PR #91 | "still plenty of graph breaks" (unquantified) | [hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91) | verified |
| 36 | torch.compile failure, `fullgraph=True` | `UserError: Could not guard on data-dependent expression Ne(Mod(310*Max(1, u0), 8), 0)` | [pytorch#149570](https://github.com/pytorch/pytorch/issues/149570) | verified |
| 37 | torch.compile failure, `fullgraph=False` | `ValueError: Pointer argument (at 0) cannot be accessed from Triton (cpu tensor?)` | [pytorch#149570](https://github.com/pytorch/pytorch/issues/149570) | verified |
| 38 | CPU → Intel XPU speedup (Kokoro, native `torch.xpu`) | **4.4x** | [hexgrad/kokoro PR #362](https://github.com/hexgrad/kokoro/pull/362) | verified |
| 39 | "16–151x" XPU speedup (crunchtools) and "3–5x" OpenVINO EP | cited in PR #362 as prior art, explicitly described as unpublished / downstream | [hexgrad/kokoro PR #362](https://github.com/hexgrad/kokoro/pull/362) | **unverified** |
| **ONNX Runtime CUDA EP** | | | | |
| 40 | 100 reqs × 8 s audio: CPU total, i7-13700KF | 136 s | [kokoro-onnx #37 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/37) | verified |
| 41 | Same workload, GPU total, RTX 4090 | 41 s | [kokoro-onnx #37 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/37) | verified |
| 42 | CUDA EP speedup vs CPU (136/41) | **3.32x** | derived from rows 40,41 | verified (derived) |
| 43 | Implied RTF, RTX 4090 (800 s audio / 41 s) | ~19.5x | derived from row 41 | verified (derived) |
| 44 | Implied RTF, CPU (800 s / 136 s) | ~5.9x | derived from row 40 | verified (derived) |
| 45 | A100: GPU **slower** than CPU, low GPU utilization | qualitative, no numbers | [kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| 46 | 39 Memcpy nodes added to graph for CUDAExecutionProvider | 39 (warning text) | [kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| 47 | ~22 s of speech, RTX 3060 Ti: int8 / fp16 / fp16-GPU / fp32 / fp32 v0.19 | 9.92 / 0.63 / 0.62 / 0.48 / 0.87 s | [kokoro-onnx #112 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| 48 | CUDA EP benefit in row 47 (0.63/0.62) | ~1.02x (≈2% — i.e. negligible; CPU fp32 was fastest) | derived from row 47 | verified (derived) |
| 49 | Same ~180-char text, Intel Core Ultra 7 258V **CPU** only | fp32 9.49 s / fp16 8.63 s / int8 27.4 s | [kokoro-onnx #112 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| **Batching** | | | | |
| 50 | nimbleEdge/kokoro documented batching throughput multiplier | **NONE — no numbers in README or repo** | [nimbleEdge/kokoro](https://github.com/nimbleEdge/kokoro) | verified absent (see §5) |
| 51 | wwang1110/kokoro_batch: batch_size=32, max_chars=200, RTX 4090, total forward pass | 1.6319 s | [wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch) | verified |
| 52 | Same run, total audio produced | 276.15 s | [wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch) | verified |
| 53 | Implied RTF of row 51/52 (276.15/1.6319) | ~169x | derived from rows 51,52 | verified (derived) |
| 54 | Documented batch-vs-serial multiplier in wwang1110/kokoro_batch | **NONE — no batch_size=1 baseline published** | [wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch) | verified absent |
| 55 | Kokoro-FastAPI realtime speed range | **35x–100x** | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 56 | Kokoro-FastAPI average processing rate | 137.67 tokens/s (cl100k_base) | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 57 | Kokoro-FastAPI hardware for rows 55,56 | WSL2, RTX 4060 Ti 16GB, CUDA 12.1, i7-11700 @2.5GHz, 64GB | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 58 | Kokoro-FastAPI long-form: full book 502,766 chars → 507m52s audio | 45.7x rt synth | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 59 | Kokoro-FastAPI first-token latency | ~300 ms GPU @chunk 400; ~3500 ms CPU @200 (older i7); <1 s CPU @200 (M3 Pro) | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 60 | Kokoro-FastAPI VRAM reclaim / reload | short: 3.11→2.37 GB, 758 MiB, +4.9 s; long: 3.98→2.37 GB, 1656 MiB, +5.1 s | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| 61 | kokoro-onnx README quantified GPU/M1 performance | **NONE — only qualitative "near real-time on macOS M1"** | [kokoro-onnx README](https://github.com/thewh1teagle/kokoro-onnx) | verified absent |

---

## 1. CoreML / Apple Silicon

**Four independent CoreML/ANE ports exist, with mutually consistent numbers.**

### FluidInference/kokoro-82m-coreml (the repo named in the brief)

Card fetched from [the raw README](https://huggingface.co/FluidInference/kokoro-82m-coreml). All four
pipelines measured on **M4 Pro, 48GB RAM, MacBook Pro**, same 11 strings, warm-up pass first,
then raw inference with models loaded:

| Backend | Audio (s) | Inference (s) | Total RTFx | Peak GB |
|---|---|---|---|---|
| PyTorch CPU | 461.650 | 27.177 | 16.987x | 4.85 |
| PyTorch MPS | 11.375 | 1.142 | 9.960x | 1.54 |
| MLX | 461.650 | 19.401 | 23.796x | 3.37 |
| Swift + FluidAudio CoreML | 404.300 | 17.408 | **23.225x** | **1.503** |

Three honest caveats from the source itself:
- **MPS crashed** on longer strings even with `PYTORCH_ENABLE_MPS_FALLBACK=1`, so only 2 of 11 tests completed — the 9.960x is not comparable to the others.
- The card states CoreML traded "lower memory and **very slightly faster inference** for longer initial warm-up" — but by its own table CoreML (23.225x) is marginally *slower* than MLX (23.796x) on this host. The real CoreML win here is memory (1.503 GB vs 3.37 GB) and the ~15 s first-run compile / ~2 s subsequent load.
- The `aoiandroid/kokoro-82m-coreml` and `aoiandroid/mirror-FluidInference-kokoro-82m-coreml` cards are **byte-identical mirrors** of this table ([aoiandroid card](https://huggingface.co/aoiandroid/kokoro-82m-coreml)) — they are not independent measurements and should not be double-counted.

### laishere/kokoro-coreml (the ancestor FluidInference credits)

[laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) — fp16+int8pal, 7 mlpackages,
measured on **M4 Mac Mini 24GB**:

| T_enc | audio_s | chain_ms | speed |
|---|---|---|---|
| 13 | 1.50 | 75.6 | 19.8x |
| 66 | 4.45 | 172.3 | 25.8x |
| 128 | 8.07 | 294.6 | 27.4x |
| 272 | 16.27 | 642.6 | 25.3x |
| 457 | 26.95 | 1047.1 | 25.7x |
| 512 | 28.12 | 1101.1 | 25.5x |

**Mean 25.0x real-time** (M4 Mac Mini), **mean 16.9x on iPhone 16 Pro**. Note the head of the
README claims "25x on M4 Mac Mini, 17x on iPhone 16 Pro" — the table backs both.

This repo also publishes the **compute-unit placement**, which is the only per-stage ANE-vs-GPU-vs-CPU
breakdown I found anywhere:

| Stage | Precision | Compute Unit |
|---|---|---|
| Albert | fp16 + int8pal | CPU_AND_NE |
| PostAlbert | fp16 + int8pal | CPU_AND_NE |
| Alignment | fp16 + int8pal | CPU_AND_NE |
| Prosody | fp16 + int8pal | ALL |
| Noise | **fp32** + int8pal | ALL |
| Vocoder | fp16 + int8pal | CPU_AND_NE |
| Tail | **fp32** | ALL |

Key design finding: **Noise and Tail must run fp32 and therefore off-ANE** (sine/cumsum phase
accumulation loses too much in fp16: correlation 0.94 → 0.82). The rest is fp16 on ANE.

### mattmireles/kokoro-coreml (the only source with a CPU-vs-GPU-vs-ANE A/B)

[mattmireles/kokoro-coreml](https://github.com/mattmireles/kokoro-coreml) — M1 Mini 16GB:

| Audio | M1 Mini (16 GB) | M2 Air (24 GB) | M2 Ultra (64 GB) |
|---|---|---|---|
| 3s | 236 ms | 200 ms | **59 ms** |
| 7s | 494 ms | 326 ms | **136 ms** |
| 15s | 1,007 ms | 783 ms | **278 ms** |
| 30s | 1,958 ms | 1,829 ms | **422 ms** |

Headline claim: "15 seconds of speech in 1.01s on an M1 Mac Mini"; "13-70x realtime across the lineup".
Stated speedups: vs PyTorch MPS 1.8–3.4x, vs PyTorch CPU 3.5–7.3x, vs Python+CoreML hybrid 1.1–2.0x.

The corrected cross-machine ledger ([bakeoff-results-v2.md](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md))
gives full RTF for four configs. M2 Ultra 64GB, warm median end-to-end:

| Input | A (Python hybrid) | D (PyTorch MPS) | E (PyTorch CPU) | F (Swift+CoreML) |
|---|---|---|---|---|
| 3s | 0.119 | 0.080 | 0.146 | **0.020** |
| 7s | 0.049 | 0.061 | 0.120 | **0.018** |
| 15s | 0.035 | 0.048 | 0.106 | **0.017** |
| 30s | 0.032 | 0.059 | 0.099 | **0.017** |

→ CoreML is **F vs MPS 2.8–4.0x** and **F vs CPU 5.7–7.2x** on M2 Ultra. On M1 Mini, F vs MPS
2.0–3.4x (v2 doc); F vs CPU computes to 4.4–7.3x, though the doc only tabulates F vs A and F vs MPS.
**PyTorch MPS OOMs at 15 s and 30 s on a 24GB M2 Air** (the doc says the MPS pool caps near 27 GB).

Attribution note on this repo: the M1 mask-aware-bucketing A/B (1.24–1.39x) carries the author's own
disclaimer — "The host did not pass the strict quiet-host gate before collection, so treat this as
engineering evidence rather than publication-grade benchmark data."

### Two further ANE data points

- [runanywhere/Kokoro-82M_ANE](https://huggingface.co/runanywhere/Kokoro-82M_ANE), **M4 Max: 14.0–26.0× realtime**, static shapes (96 phonemes, 216 frames as ANE requires). Two steps run on host, not ANE: duration→alignment expansion and the harmonic source generator.
- [soniqo/speech-swift iOS benchmark](https://github.com/soniqo/speech-swift/blob/7690825d1d717cf071c7a4b4fd8ab683db4d5acc/docs/benchmarks/ios-coreml.md), **iPhone 16 Pro (A18 Pro), iOS 26.5: Kokoro-82M CoreML = 0.08 RTF, 676 MB peak** (median of 5 timed runs after warm-up). Their RTF convention is inverted (wall/audio, lower is better), so this is **~12.5x realtime** — noticeably below laishere's 16.9x on the same phone model. Different harness, different model packaging; the discrepancy is unexplained by either source.

**Bottom line for §1:** CoreML/ANE on Apple Silicon is a real, reproducible 14–27x realtime
proposition, and ~1.4x faster than PyTorch CPU on M4 Pro while using ~3x less memory. But it is
**not dramaticaly faster than MLX** (23.2x vs 23.8x on M4 Pro — a wash), and its clear wins over
PyTorch **GPU** (MPS) are 2.0–4.0x, not 10x+.

---

## 2. TensorRT — NO PUBLISHED BENCHMARK EXISTS (this is itself the finding)

I searched repo search, HF discussions, and the open web for "Kokoro TensorRT", "kokoro trt",
Kokoro TRT engine benchmarks, and TensorRT-LLM Kokoro conversions. **I found zero published
TensorRT latency or throughput numbers for Kokoro-82M.** What I found instead is two independent
conversion *failures* and one explicit statement that Kokoro is the hardest case:

1. [thewh1teagle/kokoro-onnx issue #50 "TensorRT support"](https://github.com/thewh1teagle/kokoro-onnx/issues/50) — open since 2025-01-18, still open, assigned to maintainer. The TRT EP fails at session init:
   > `TensorRT input: /decoder/decoder/generator/istft/stft/Gather_output_0 has no shape specified. Please run shape inference on the onnx model first.`
   The reporter notes "I tried manually optimizing the onnx model but that doesn't seem to help" and speculates "TensorRT could be even faster than CUDA it seems" — speculation, no measurement.

2. [HF discussion onnx-community/Kokoro-82M-v1.0-ONNX #15 "Tensorrt Support"](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/discussions/15) (2025-09-29, open, 1 comment, no reply from maintainers). Direct `trtexec` attempt with **TensorRT 8.6.13 on a Jetson Orin** (compute capability 8.7, 16 SMs, 28953 MiB):
   > `Assertion failed: !isDynamic(shape) && "Cannot infer squeeze dimensions from a dynamic shape! Please re-export your model with the Squeeze axes input set."`
   > `Failed to create engine from model or file.` / `Engine set up failed`
   Also blocked by **unsupported ops**: `SequenceAt`, `SequenceInsert`, `SplitToSequence`. No benchmark was reached.

3. [vieenrose/jetson-tts PR #1](https://github.com/vieenrose/jetson-tts/pull/1) ports only *vocoders* to TRT and states the reason explicitly: full TTS graphs have data-dependent shapes that TRT cannot build, and **"Kokoro is hardest (Loop/If + fused source-filter, no clean boundary)."** Its measured numbers — MeloTTS-8k 154.6 ms → 22.1 ms (**7.0×**), Matcha-8k 208.5 ms → 32.2 ms (**6.5×**) on Jetson Nano gen1, JetPack 4.5 / TRT 7.1.3, FP16, clocks pinned — are **for other models, not Kokoro.** Do not attribute them to Kokoro.

**Conclusion:** the blocker is structural (dynamic shapes / data-dependent control flow in the
StyleTTS2-derived alignment and iSTFT path), not a missing recipe. Anyone wanting a Kokoro TRT
number would have to publish the first one.

---

## 3. torch.compile — small documented win, then real breakage

### The documented speedup (about 1.34x, on one GPU, one sentence)

[hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91), by gau-nernst, **merged 2025-02-15**.
Root cause fixed: `np.prod()` returns a numpy type that PyTorch turns into a FakeTensor during
compilation, breaking `F.upsample()`:
> `TypeError: upsample_linear1d() received an invalid combination of arguments - got (Tensor, NoneType, bool, list)`

Fix was a one-line `np.prod()` → `math.prod()`. Measured, **RTX 4070 Ti SUPER**, 100 iterations of one sentence:
```
Eager:   4.20 ms
Compile: 3.13 ms
```
→ **1.34x** (derived). The PR is candid that this is not a clean compile: "there are still plenty of
graph breaks (I will address this in subsequent PRs)." So: a ~26% per-call gain, thermally and
methodologically unverified beyond the author's own box, on a GPU, with unquantified graph breaks.

### It still fails — filed on PyTorch itself a month later

[pytorch/pytorch issue #149570 "[ued][kokoro] torch.compile fails in kokoro (both fullgraph=True and False)"](https://github.com/pytorch/pytorch/issues/149570), filed 2025-03-19 by yushangdi, **closed 2025-06-18**. Both modes fail, with different errors:

- `fullgraph=True` → `torch._dynamo.exc.UserError: Could not guard on data-dependent expression Ne(Mod(310*Max(1, u0), 8), 0)`, originating in `torch.nn.functional.scaled_dot_product_attention` inside `transformers/models/albert/modeling_albert.py`.
- `fullgraph=False` → `ValueError: Pointer argument (at 0) cannot be accessed from Triton (cpu tensor?)`, from a Triton `benchmark_gpu` autotune on a CPU tensor.

Note the internal tension: PR #91 (merged Feb 2025) made Kokoro compile-able, yet this PyTorch issue
(filed Mar 2025) shows it failing on `kokoro>=0.9.2` + nightly cu126. The two are not necessarily
contradictory (different configs/text lengths/dates), but **no source I found reports a clean,
sustained torch.compile win on Kokoro.** Treat 1.34x as the optimistic single-point figure.

### Adjacent: Intel XPU (not torch.compile, but an accelerated PyTorch backend)

[hexgrad/kokoro PR #362](https://github.com/hexgrad/kokoro/pull/362) (open, not merged) adds native
`torch.xpu` support: **"Measured 4.4x speedup cpu ~> xpu with this fix (excluding warmup round)."**
The author explicitly flags it is "a same-shape, single-sentence, steady-state number, not a
line-for-line reproduction of the larger third-party figures cited above (different text lengths per
call reintroduce shape-recompile overhead — not measured here)."

That PR's "prior art" section cites **3–5x for the OpenVINO EP** (Unicorn-Orator, magicunicorn) and
**16–151x for native PyTorch xpu** (crunchtools). The PR itself describes the crunchtools patch as
"never published as code" and "described in prose only", and the OpenVINO numbers are attributed to
downstream wrappers. **I could not verify either figure from a primary source → marked unverified.**

---

## 4. ONNX Runtime CUDAExecutionProvider — numbers exist, but they disagree

The `thewh1teagle/kokoro-onnx` README publishes **no** GPU numbers (only "[Fast performance near
real-time on macOS M1](https://github.com/thewh1teagle/kokoro-onnx)", which is qualitative and about
M1 CPU/GPU, ambiguous). All usable CUDA EP figures are **user-reported issue comments**, not repo
benchmarks. They are inconsistent with each other and that inconsistency is the real finding:

**Positive result — RTX 4090, [kokoro-onnx #37, comment 2594658817](https://github.com/thewh1teagle/kokoro-onnx/issues/37) (ak01user, 2025-01-16):**
> "I simulated 100 requests, each generating 8 seconds of audio. It took 136 seconds in CPU mode and 41 seconds on the GPU. My device has an i7-13700KF CPU and an RTX 4090 GPU."

→ CUDA EP **3.32x** faster than CPU; implied ~19.5x realtime on GPU vs ~5.9x on CPU (both derived).

**Negative result — A100, [kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) (lhr-30, 2025-02-19), still open:**
> "I use A100 to host the model. I found that when I use GPU, it is even slower than using CPU. The GPU utilization is also low."

The ORT log in that issue shows why: `39 Memcpy nodes are added to the graph main_graph for
CUDAExecutionProvider. It might have negative impact on performance (including unable to run CUDA
graph)` plus shape-related ops pinned to CPU. **No numbers given.**

**Negative result — RTX 3060 Ti, [same issue, comment 2675987919](https://github.com/thewh1teagle/kokoro-onnx/issues/112) (fedirz, 2025-02-22)**, ~22 s of speech:

| Config | Time |
|---|---|
| int8 | 9.92 s |
| fp16 (CPU) | 0.63 s |
| **fp16 (GPU)** | **0.62 s** |
| fp32 (CPU) | **0.48 s** |
| fp32 v0.19 | 0.87 s |

→ CUDA EP bought **~2%** over CPU fp16 (0.63→0.62), and **plain fp32 on CPU beat every GPU run**.
Note also that **int8 was catastrophically slow (9.92 s, ~16x worse than fp16)** — confirmed
independently on CPU by another reporter ([comment 3162218088](https://github.com/thewh1teagle/kokoro-onnx/issues/112), xiaohoua, 2025-08-07, Intel Core Ultra 7 258V: fp32 9.49 s / fp16 8.63 s / **int8 27.4 s**).

**Interpretation:** Kokoro-82M is small enough that ORT CUDA EP overhead (graph partitioning,
Memcpy nodes, CPU-pinned shape ops, per-call launches) can exceed the compute saved. Reported
speedup spans **0.5x (slower) to 3.3x**. There is no authoritative CUDA EP benchmark; do not quote a
single multiplier without the hardware and — critically — the quantization level, since int8
quantization dominates the result far more than the execution provider does. I found **no
r/LocalLLaMA or blog benchmark** of Kokoro under CUDAExecutionProvider with RTF figures.

---

## 5. Batching throughput multipliers — the specific numbers mostly do not exist

### nimbleEdge/kokoro — no numbers at all (checked exhaustively)

[README.md](https://raw.githubusercontent.com/nimbleEdge/kokoro/main/README.md) documents batched
inference only qualitatively: "This implementation supports batched inference and can be exported to
ONNX for optimized deployment"; feature list says "Support for batched inference". The Quick Start
shows `forward_with_tokens(input_ids, 1.0, input_lengths)` with `rnn.pad_sequence` padding.

**There is no benchmark section, no RTF, no speedup, no hardware statement, and no "Nx faster at
batch size 8" claim.** I also enumerated the entire repo tree
([`git/trees/main?recursive=1`](https://api.github.com/repos/nimbleEdge/kokoro/git/trees/main?recursive=1)):
it contains only `README.md`, `LICENSE`, `export_onnx.py`, `main.py`, `on_device_workflow.py`,
`pyproject.toml`, `output.wav`, `kokoro/{__init__,custom_stft,istftnet,model,modules,tokenizer}.py`,
`misaki_lexicons/`, and `voices/*.bin`. **No results, benchmark, or metrics file of any kind.**
The perf claims in this repo are unquantified.

### wwang1110/kokoro_batch — real numbers, but no baseline (so no multiplier)

[wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch) is the only batched fork I found
that publishes measured output. Its Performance section, **RTX 4090, `max_chars=200`, `batch_size=32`**:

```
INFO:root:Total forward pass duration: 1.6319s
INFO:__main__:Total audio duration: 276.15 seconds.
```

That is ~169x realtime for a batch of 30 items (derived: 276.15 / 1.6319). Useful as an absolute
throughput figure — **but the repo publishes no `batch_size=1` baseline, so there is no documented
batching multiplier.** The per-stage breakdown is given (final iSTFT processing 1.1648 s dominates
at 71% of the forward pass; source generation only 0.0451 s) — i.e. the vocoder/iSTFT tail is the
bottleneck, not the text encoder, which is why batching the encoder alone would not help much.

### Kokoro-FastAPI — strong throughput figures, but not framed as a batching multiplier

[remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) README, measured on **Windows 11
WSL2, RTX 4060 Ti 16GB @ CUDA 12.1, i7-11700 @2.5GHz, 64GB RAM**, WAV output, full text of
*The Time Machine*:

- Realtime speed: **35x–100x**
- Average processing rate: **137.67 tokens/second** (cl100k_base)
- Long-form roundtrip: short (~ch.7) 64,996 chars → 66m06s audio at **36.4x rt**; full book 502,766 chars → 507m52s audio at **45.7x rt**
- First-token latency: **~300 ms GPU @ chunk 400**; ~3500 ms CPU @200 (older i7); **<1 s CPU @200 (M3 Pro)**
- Model unload/reload: short (6 s audio) 3.11→2.37 GB, 758 MiB reclaimed, +4.9 s reload; long-form (7.5 m) 3.98→2.37 GB, 1656 MiB reclaimed, +5.1 s
- Chunking defaults `TARGET_MIN_TOKENS=175`, `TARGET_MAX_TOKENS=250`, `ABSOLUTE_MAX_TOKENS=450`

Note this is the **PyTorch+CUDA** path, not ONNX Runtime. Also relevant to the brief's batching
question: the README documents that **many voices cost little**, but "if each speaker gets less than
about 2 sentences, chunking requirements slow generation down. Still a flat cost, not compounding as
the text grows."

**No Kokoro-FastAPI document states a batch-size-N multiplier.** Its batching-adjacent gains are
reported as chunk-size and streaming-parameter effects, not as "Nx at batch size 8".

---

## Explicit gaps (things the brief asked for that do not exist)

1. **No Kokoro-82M TensorRT latency/throughput number is published anywhere I could find.** Two attempts fail on dynamic shapes; one project states Kokoro is the hardest TRT case.
2. **nimbleEdge/kokoro publishes zero performance numbers** — no RTF, no multiplier, no hardware.
3. **No batched Kokoro implementation publishes a batch-N-vs-batch-1 multiplier.** wwang1110/kokoro_batch gives absolute batch-32 throughput only; Kokoro-FastAPI gives RTF/tokens-per-second only.
4. **No ANE-vs-GPU-vs-CPU head-to-head at fixed model/input** exists. The closest proxies are mattmireles' MPS/CPU/ANE bakeoff (2.0–4.0x over MPS, 4.4–7.3x over CPU) and FluidInference's M4 Pro table, which compares CoreML to MLX/CPU but **not to MPS at full length** (MPS crashed).
5. **No r/LocalLLaMA or blog-post CUDA EP benchmark** with RTF was found; the only CUDA EP numbers are GitHub issue comments that range from 3.32x faster (RTX 4090) to slower than CPU (A100).
6. **The "16–151x" XPU and "3–5x" OpenVINO claims** circulating in kokoro PR #362 are **unverified** — one is explicitly unpublished, both are attributed to downstream/third-party work.
