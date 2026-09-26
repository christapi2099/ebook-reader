# Kokoro-82M on an NVIDIA T600 Laptop (4 GB, Turing cc 7.5) and on CPU

Focused web research. Every claim is cited. Anything I could not confirm from a primary
source is marked **unverified**. Where a number is my own arithmetic on verified values it is
marked **derived** with the arithmetic shown.

**Evidence-quality warning up front.** Almost nothing here is a controlled, apples-to-apples
benchmark. The usable numbers come from three very different places:

- a handful of rigorous, self-published harnesses (the `efemaer` gist, the `tts-ptq-map` paper
  repo, the Kokoro-FastAPI README's own measurements);
- **GitHub issue comments** — i.e. one user, one machine, no methodology — which is where most
  of the GPU numbers live;
- vendor/SEO content sites (smeltcore.com, spheron.network) that themselves cite secondary
  sources and hedge with "estimates". I have marked those **unverified** rather than laundering
  them into facts.

The RTF conventions also disagree between sources. I state the convention for each number.

---

## Numbers table

### Model size and VRAM footprint

| Claim | Value | Source | Status |
|---|---|---|---|
| Kokoro-82M parameter count | 82M | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | verified |
| ONNX fp32 weight file | 326 MB | [onnx-community card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX) | verified |
| ONNX fp16 weight file | 163 MB | [same](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX) | verified |
| PyTorch fp32 weights (~328 MB) / fp16 (~164 MB) | 82M x 4 B / x 2 B | derived from 82M params | derived |
| Kokoro-FastAPI baseline VRAM (CUDA) | **1020 MiB** | [Kokoro-FastAPI #15](https://github.com/remsky/Kokoro-FastAPI/issues/15) | verified |
| Same, after `CLEAR_CUDA_CACHE=true` + `N_CACHE_VOICES=1` | **800 MiB** | [#15 comment](https://github.com/remsky/Kokoro-FastAPI/issues/15) | verified |
| Same server, later report (2026-01) | **1616 MiB** | [#15 comment](https://github.com/remsky/Kokoro-FastAPI/issues/15) | verified |
| CUDA context + host **floor** | **2.37 GB** | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified (WSL2) |
| Loaded, short workload (6 s audio) | **3.11 GB** | [same](https://github.com/remsky/Kokoro-FastAPI) | verified (WSL2) |
| Loaded, long-form (7.5 min audio) | **3.98 GB** | [same](https://github.com/remsky/Kokoro-FastAPI) | verified (WSL2) |
| "weights under 1 GB at FP16, total 2-3 GB during inference" | 2-3 GB | [Spheron blog](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/) | **unverified** (est.) |
| VRAM "minimum 2 GB, recommended 4 GB"; "< 2 GB VRAM" | 2 / 4 GB | [Clore.ai guide](https://docs.clore.ai/guides/audio-and-voice/kokoro-tts.md) | **unverified** (guidance, not a measurement) |
| CUDA EP VRAM spikes during inference | qualitative | [kokoro-onnx #37](https://github.com/thewh1teagle/kokoro-onnx/issues/37) | verified (report only) |

### Throughput / RTF

| Claim | Value | Source | Status |
|---|---|---|---|
| **PyTorch CPU**, Xeon Platinum 8272CL (4 cores), 32 threads | RTF 0.7865 -> **1.3x** realtime | [gauravvij repo](https://github.com/gauravvij/kokoro-tts-vs-supertonic-3-tts) | verified |
| **ONNX CPU**, same host | RTF 0.5711 -> **1.8x** realtime | [same](https://github.com/gauravvij/kokoro-tts-vs-supertonic-3-tts) | verified |
| ONNX CPU vs PyTorch CPU speedup there | ~1.38x (0.7865/0.5711) | derived | derived |
| **CPU**, c6a.8xlarge (32 vCPU EPYC 7R32), PyTorch and ONNX | **5x** realtime (both) | [efemaer gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653) | verified |
| **CPU**, Mac mini M4 Pro, 4 pinned threads, fp32 | proc/audio **0.0692** -> ~**14.5x** | [tts-ptq-map](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md) | verified |
| Same, ONNX int8 dynamic (`kokoro_dyn8`) | **0.0772** -> ~13.0x (**slower than fp32**) | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md) | verified |
| Same, W4 weights | **0.0695** (no gain vs fp32) | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md) | verified |
| CPU peak RSS, Mac mini M4 Pro | 2712 MB (fp32) | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md) | verified |
| **NVIDIA T4** (cc 7.5, same generation as T600), PyTorch CUDA | **36x** realtime | [efemaer gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653) | verified |
| **NVIDIA T4**, ONNX CUDA EP | **20x** realtime (**slower than PyTorch**) | [same](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653) | verified |
| A10G, PyTorch CUDA / ONNX CUDA | 96x / 32x | [same](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653) | verified |
| L4, PyTorch CUDA / ONNX CUDA | 81x / 37x | [same](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653) | verified |
| ONNX CUDA vs PyTorch CUDA on T4 | **0.56x** (ONNX 20 vs PyTorch 36) | derived | derived |
| **Colab T4**, full novel (~160k chars) via audiblez | **~5 min, ~600 chars/s** | [audiblez README](https://github.com/santinic/audiblez) | verified |
| M2 MacBook Pro **CPU**, same task | ~1 h, ~**60 chars/s** | [same](https://github.com/santinic/audiblez) | verified |
| T4 vs M2 CPU throughput | ~10x | derived (600/60) | derived |
| RTX 4090, 100 req x 8 s audio: CPU 136 s vs GPU 41 s | **3.32x** GPU speedup | [kokoro-onnx #37](https://github.com/thewh1teagle/kokoro-onnx/issues/37) | verified |
| Implied realtime factor there | ~5.9x CPU / ~19.5x GPU | derived (800 s audio) | derived |
| RTX 3060 Ti, ~22 s speech: int8 / fp16 / fp16-gpu / fp32 | **9.92 / 0.63 / 0.62 / 0.48 s** | [kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified (provider ambiguous) |
| int8 vs fp16 on that GPU | **~16x slower** (9.92/0.63) | derived | derived |
| CPU-only, Intel Core Ultra 7 258V: fp32 / fp16 / int8 | 9.49 / 8.63 / **27.4 s** | [#112 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| A100: "GPU even slower than using CPU", low utilization | qualitative | [#112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) | verified |
| RTX 4060 Ti 16GB via Kokoro-FastAPI | **35x-100x** realtime | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| Same, average processing rate | 137.67 tokens/s (cl100k_base) | [same](https://github.com/remsky/Kokoro-FastAPI) | verified |
| Same, full book 502,766 chars -> 507m52s audio | **45.7x** realtime | [same](https://github.com/remsky/Kokoro-FastAPI) | verified |
| A100 RTF ~0.03; RTX 4090 ~0.04-0.06 | estimates | [Spheron](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/) | **unverified** |
| **NVIDIA T600 (4 GB) Kokoro benchmark** | **does not exist** | — | **verified absent** |

### CoreML / Apple Silicon

| Claim | Value | Source | Status |
|---|---|---|---|
| PyTorch CPU agg RTFx, M4 Pro 48 GB | 16.987x | [FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| MLX agg RTFx, same host | 23.796x | [same](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| Swift + CoreML agg RTFx, same host | 23.225x | [same](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| PyTorch MPS agg RTFx (crashed; only 2/11 tests ran) | 9.960x | [same](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| CoreML vs PyTorch CPU | ~1.37x | derived | derived |
| CoreML vs MLX | ~0.98x (a wash) | derived | derived |
| Peak memory CoreML vs PyTorch CPU | **1.503 GB** vs 4.85 GB | [same](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| CoreML first-run compile / later loads | ~15 s / ~2 s | [same](https://huggingface.co/FluidInference/kokoro-82m-coreml) | verified |
| Mean 6-passage RTFx, CoreML, M4 Mac Mini 24 GB | 25.0x (19.8-27.4x) | [laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml) | verified |
| iPhone 16 Pro mean RTFx, same chain | 16.9x | [same](https://github.com/laishere/kokoro-coreml) | verified |
| CoreML vs PyTorch MPS / CPU, M2 Ultra | 2.8-4.0x / 5.7-7.2x | [mattmireles bakeoff](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| PyTorch MPS OOM on 24 GB M2 Air | OOM at 15 s and 30 s | [same](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md) | verified |
| CoreML must keep Noise/Tail in **fp32, off-ANE** | corr 0.94 -> 0.82 in fp16 | [laishere](https://github.com/laishere/kokoro-coreml) | verified |

### Quantization

| Claim | Value | Source | Status |
|---|---|---|---|
| Kokoro fp32 UTMOS / WER | **4.52 / 0.030** | [tts-ptq-map RESULTS](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W4 per-channel: dUTMOS / WER | **-0.07 / 0.030** (near-lossless) | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W4 group:128: dUTMOS / WER | **-0.04 / 0.029** | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W4 **per-tensor**: dUTMOS | **-3.15** (catastrophic) | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W6: dUTMOS | **-0.003** | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W8 + simulated int8 acts, per-channel | -0.07 | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| W4 + simulated int8 acts, per-channel g128 | -0.13 | [same](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md) | verified |
| `model_q4.onnx` is **305 MB** (almost fp32-sized) | 305 vs 326 MB | [onnx-community](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX) | verified |
| Quantized models do **not** run on CoreML | qualitative | [kokoro-en crate](https://docs.rs/crate/kokoro-en/0.1.2) | verified |
| `q8f16` size disagreement (86 MB card vs ~160 MB crate) | conflict | [card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX) vs [crate](https://docs.rs/crate/kokoro-en/0.1.2) | **conflicting** |

### Batching

| Claim | Value | Source | Status |
|---|---|---|---|
| `KPipeline.__call__` has a `batch_size` parameter | **No such parameter exists** | [pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py) | verified |
| `KModel.forward` accepts a batch | **No** — hardcodes `torch.LongTensor([[0, *input_ids, 0]])` | [model.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/model.py) | verified |
| `forward_with_tokens` is batch-correct | **No** — `pred_aln_trg` is built with `.unsqueeze(0)` and `pred_dur` uses `.squeeze()` | [model.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/model.py) | verified |
| Batch inference request in hexgrad/kokoro | issue **still open** since 2025-02-05, 7 comments, no upstream impl | [#55](https://github.com/hexgrad/kokoro/issues/55) | verified |
| ONNX batch/TRT PR merged into upstream | merged 2025-07-26, but **touches `examples/` only** | [#239](https://github.com/hexgrad/kokoro/pull/239) | verified |
| nimbleEdge/kokoro (batch fork) published multiplier | **None — zero numbers anywhere in repo** | [nimbleEdge/kokoro](https://github.com/nimbleEdge/kokoro) | **verified absent** |
| wwang1110/kokoro_batch, batch_size=32, RTX 4090 | forward pass 1.6319 s -> 276.15 s audio (~169x) | [repo](https://github.com/wwang1110/kokoro_batch) | verified |
| Same, batch-vs-serial multiplier | **None published** (no batch_size=1 baseline) | [same](https://github.com/wwang1110/kokoro_batch) | **verified absent** |
| iSTFT share of that forward pass | 1.1648 s / 1.6319 s = **71%** | derived | derived |

### Turing / fp16 / ORT CUDA

| Claim | Value | Source | Status |
|---|---|---|---|
| T600 compute capability | **7.5** | [NVIDIA CUDA GPUs list](https://developer.nvidia.com/cuda/gpus) | verified |
| **T600 has NO tensor cores** (TU117 die) | 0 tensor cores | [NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html), [Wikipedia Turing dies](https://en.wikipedia.org/wiki/Turing_(microarchitecture)) | verified (2 sources) |
| T600 die / CUDA cores / VRAM / bus | TU117, 896 cores @1.4 GHz, 4 GB GDDR6, 128-bit | [NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html) | verified (secondary) |
| T600 bandwidth / FP32 peak | 160 GB/s / 2.5 TFLOPS | [same](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html) | verified (secondary, arithmetic-consistent) |
| T600 TDP | 40 W (spec block) vs 25 W (prose) | [same](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html) | **conflicting — cite neither** |
| T600 PCIe generation | "PCIe 4.0 x8" vs Wikipedia "PCIe 3.0" | [both](https://en.wikipedia.org/wiki/Turing_(microarchitecture)) | **conflicting — unverified** |
| NVIDIA T600 Laptop datasheet numbers | not obtainable (PDF 404s) | [nvidia.com PDF](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/documents/nvidia-t600-datasheet.pdf) | **unverified** |
| Turing has **no** native BF16 | "Bfloat16-precision FP ops: **No** for 7.x" | [CUDA Programming Guide](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html) | verified |
| BF16 + TF32 both arrive at | Ampere SM80 | [NVIDIA CUTLASS docs](https://mintlify.wiki/NVIDIA/cutlass/concepts/tensor-cores) | verified |
| Turing tensor-core input types | FP16, INT8, INT4, INT1 (no BF16/TF32) | [same](https://mintlify.wiki/NVIDIA/cutlass/concepts/tensor-cores) | verified (moot on T600) |
| ONNX Runtime fp16 conv speedup requires tensor cores | "if the hardware supports tensor core operations" | [ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) | verified |
| SineGen phase accumulation breaks in fp16 | corr vs fp32 falls to **0.006** within 10 s at F0=200 Hz | [hexgrad/kokoro PR #353](https://github.com/hexgrad/kokoro/pull/353) | **unverified** (open, unmerged, third-party, AI-authored) |
| fp16 corrupts band energies on CUDA (legacy source) | 3.2 / 5.0 / 2.9 dB over thirds of a 36 s utterance | [same](https://github.com/hexgrad/kokoro/pull/353) | **unverified** (same) |
| "half-precision deployments currently pin the decoder to fp32" | qualitative | [same](https://github.com/hexgrad/kokoro/pull/353) | **unverified** (same) |
| Kokoro fp16 fails on Intel GPU | `ScatterNDUpdate` unsupported for F16 | [Intel KB 000101364](https://www.intel.com/content/www/us/en/support/articles/000101364/software.html) | verified |
| fp16 ONNX export needs ORT converter (Loop subgraph Cast mismatch) | qualitative | [kokoro-onnx PR #198](https://github.com/thewh1teagle/kokoro-onnx/pull/198) | verified |
| fp16-storage / fp32-compute: 325 MB -> 162 MB, audio bit-identical | qualitative | [soniqo commit](https://huggingface.co/soniqo/Kokoro-82M-ONNX/commit/0132fba59f8113ec70c9855266724d41f5cdffc0) | verified |
| FP16 autocast regression at small batch (A10, Ampere) | **10x slower at bs=1**, 2.7x at bs=2 | [pytorch#159346](https://github.com/pytorch/pytorch/issues/159346) | verified |
| ORT CUDA EP requires cuDNN, matching major version | "not compatible with cuDNN 9.x, and vice versa" | [ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) | verified |
| ORT CUDA EP: is cc 7.5 in official builds | yes — Windows arch list includes `75-real` | [ORT #28762](https://github.com/microsoft/onnxruntime/issues/28762) | verified |
| ORT CUDA EP minimum compute capability | **no documented minimum found** | [ORT docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) | **not found** |
| TensorRT for Kokoro: any published benchmark | **None exists** | [kokoro-onnx #50](https://github.com/thewh1teagle/kokoro-onnx/issues/50), [HF disc #15](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/discussions/15) | **verified absent** |
| TensorRT conversion attempts | both **fail** on dynamic shapes | [same](https://github.com/thewh1teagle/kokoro-onnx/issues/50) | verified |
| `torch.compile` speedup | 4.20 -> 3.13 ms = **1.34x** (RTX 4070 Ti SUPER) | [hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91) | verified single point |
| `torch.compile` on Kokoro in `pytorch` | **fails in both modes** | [pytorch#149570](https://github.com/pytorch/pytorch/issues/149570) | verified |
| fp16/Turing CPU-core fp16-vs-fp32 crossover threshold | **no citable number exists** | — | **verified absent** |

| CUDA context overhead per process (mainstream figure) | **~300-500 MB** | [dev.to VRAM budget](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2) | blog, indicative |
| Browser HW acceleration VRAM drift | "a couple of GB" depending on tabs | [same](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2) | blog, vague |
| **Native-Windows Kokoro VRAM floor** | **not measured anywhere** | — | **verified absent** |

### GPU contention / VRAM pressure

| Claim | Value | Source | Status |
|---|---|---|---|
| NVIDIA shared-memory fallback exists (driver 536.40+) | apps "continue to run, albeit at lower speeds" | [NVIDIA KB 5490 via Wayback](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | verified (vendor primary) |
| Off switch for the fallback | `CUDA - Sysmem Fallback Policy → Prefer No Sysmem Fallback` (546.01+) | [same](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | verified |
| NVIDIA-published sysmem-fallback penalty | **none published** ("lower speeds" only) | [same](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | **verified absent** |
| Spill is silent | `cudaMalloc` returns success; nothing raises; no NVML/nvidia-smi field | [unsloth commit](https://github.com/unslothai/unsloth/commit/3c69ecaa6add633a40ba923df9f56899aa3ff61c) | engineering claim |
| Measured spill penalty | 18.0 -> 0.9 tok/s (**~20x**) | [ollama#16020](https://github.com/ollama/ollama/issues/16020) | third-party, 1 user |
| Spill penalty (broader claim) | "10x to 100x slow with a clean log" | [unsloth commit](https://github.com/unslothai/unsloth/commit/3c69ecaa6add633a40ba923df9f56899aa3ff61c) | unverified estimate |
| **Mode switch invalidates the CUDA context** | "any call to the CUDA runtime to fail and return an **invalid context error**" | [CUDA C++ Programming Guide §6.5](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified (vendor primary) |
| Display primary surface steals DRAM; mode switch may "cannibalize" CUDA allocations | qualitative | [same](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| TDR default timeout (NVIDIA's wording) | **2 seconds**; recommends 10 s, not disabling | [Nsight VSE docs](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm) | verified |
| After a TDR | "grid launch failure, and the `CUcontext` will begin to report errors" | [same](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm) | verified |
| **Kernels >2 s on WDDM2 without TDR** (Win10 RS4+, Pascal+) | "A kernel can now run for more than 2s on WDDM2 without hitting a TDR" | [NVIDIA GTC 2019 S9957](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) | verified (vendor primary) |
| Preemption works between graphics and compute | long kernels "preemptible so the graphics apps will stay responsive" | [same](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) | verified |
| Compute preemption supported since | Pascal onward (cc 6+), instruction-level, auto-enabled | [CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| TCC mode on GeForce | GeForce (excl. Titan) "do not support TCC mode"; TCC removes display output | [CUDA Windows install guide](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html) | verified |
| Whether T600 *Laptop* accepts TCC | **unverified** — check `nvidia-smi -q` locally | — | **unverified** |
| GUI stutter with short kernels on display GPU | GUI stutters at only **30-50 ms** kernels | [NVIDIA forums #382941](https://forums.developer.nvidia.com/t/general-question-should-models-be-structured-to-fit-under-frame-budgets-when-running-ml-work-on-display-gpu/382941) | forum report, 1 user |
| Older display-GPU stutter reports | kernels ~1 s -> desktop lag, cursor stutter; >2 s -> freeze | [NVIDIA forums #9718](https://forums.developer.nvidia.com/t/effect-of-cuda-on-primary-display-device-slow-does-of-desktop-with-some-code/9718), [#63834](https://forums.developer.nvidia.com/t/cuda-accelerated-program-running-on-display-gpu-freezes-system/63834) | forum reports (2010-2018) |
| AMD analogue: stutter past ~90% combined VRAM | "significant display stuttering"; **not** reproduced on RTX A4000 | [ollama#10229](https://github.com/ollama/ollama/issues/10229) | third-party, AMD |
| Audio underruns from Graphics Kernel latency | max ISR 72,906 µs (`dxgkrnl.sys`), max DPC 73,794 µs | [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5534656/experiencing-audio-and-video-stuttering-from-dxgkr) | verified latency numbers, cause unestablished |
| CUDA context overhead per process (mainstream figure) | **~300-500 MB** | [dev.to VRAM budget](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2) | blog, indicative |
| Browser HW acceleration VRAM drift | "a couple of GB" depending on tabs | [same](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2) | blog, vague |
| **Native-Windows Kokoro VRAM floor** | **not measured anywhere** | — | **verified absent** |
| Windows/WDDM "invisible" GPU reserve | **~1.3 GiB** (WSL2) | [club-3090 FAQ](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md) | community source, indicative |
| Desktop-session VRAM cost | safe ceiling drops `0.985` -> `0.92-0.94` on 24 GB (~**1.1-1.6 GB**) | [same](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md) | community source, indicative |
| Named VRAM holders | compositor, browser GPU accel, stray Python/container | [same](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md) | community source |
| TDR default timeout (Microsoft's wording) | **2 seconds** (`TdrDelay`) | [Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified (vendor primary) |
| TDR default recovery level | `TdrLevelRecover` (recover on timeout) | [same](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| TDR: 5 events in 60 s crashes the machine | `TdrLimitCount=5`, `TdrLimitTime=60` | [same](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| Microsoft position on raising TdrDelay | "End users shouldn't manipulate these registry keys" | [same](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| T600 Laptop bandwidth | **conflicting: ~160 vs 192 GB/s** | [NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html) vs [CpuTronic](https://cputronic.com/gpu/nvidia-t600-mobile) | **conflicting — do not quote a single figure** |
| TDR report attributed to Kokoro or any small TTS | **none found** | — | **verified absent** |
| Quantified desktop stutter on 4 GB laptops from CUDA pressure | **none found** | — | **verified absent** |
| xrun/JACK failure linked to CUDA compute load | **no evidence found** | — | **verified absent** |

### Streaming / chunk boundaries

| Claim | Value | Source | Status |
|---|---|---|---|
| Kokoro phoneme context limit | **510 tokens** (512 minus BOS/EOS) | [pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py) | verified |
| Default `split_pattern` | `r'\n+'` | [pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py) | verified |
| Chunk boundary placement heuristic | `waterfall_last`, splits on `!.?…` then `:;` then `,` | [pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py) | verified |
| Running the model at 510 tokens causes "'rushed' speech and other artifacts" | qualitative | [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) | verified |
| Smaller chunks increase intonation artifacts | "Artifacts in intonation can increase with smaller chunks" | [same](https://github.com/remsky/Kokoro-FastAPI) | verified |
| Server-side chunk targets | `TARGET_MIN_TOKENS=175`, `TARGET_MAX_TOKENS=250`, `ABSOLUTE_MAX_TOKENS=450` | [same](https://github.com/remsky/Kokoro-FastAPI) | verified |
| Abbreviation-aware chunking is broken | "unwanted long pause in the middle of some sentences" | [Kokoro-FastAPI #308](https://github.com/remsky/Kokoro-FastAPI/issues/308) | verified |
| Non-English G2P "chunking logic not yet implemented" | warning emitted at runtime | [pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py) | verified |
| Kokoro "clicks at the end of its generation" | qualitative | [TTS.cpp #39](https://github.com/mmwillet/TTS.cpp/issues/39) | verified |
| Streaming TTFB optimisation (chunk 436 KB -> 71.5 KB) | 2 s -> ~100 ms first play | [neosun100 doc](https://github.com/neosun100/kokoro-tts/blob/main/docs/STREAMING_OPTIMIZATION.md) | verified (single project) |

---

## 1. Model size and VRAM footprint

**Is it comfortably under 4 GB? On paper yes; in practice on Windows/WSL2 it is tight.**

The weights are genuinely tiny. The fp32 ONNX export is **326 MB** and the fp16 export is
**163 MB** ([onnx-community card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)),
consistent with 82M params at 4 and 2 bytes each. That is not the problem.

The problem is everything around the weights. The most useful real measurement is
Kokoro-FastAPI's own model-unload study, which separates the *floor* from the *loaded* state
([README](https://github.com/remsky/Kokoro-FastAPI)):

| Workload | Loaded | Floor | Reclaimed | Reload |
|---|---|---|---|---|
| Short (6 s audio) | 3.11 GB | **2.37 GB** | 758 MiB | +4.9 s |
| Long-form (7.5 min) | **3.98 GB** | **2.37 GB** | 1,656 MiB | +5.1 s |

The README states the floor "is host + CUDA context". **This is the single most important
number in this report for a 4 GB card.** A 2.37 GB CUDA-context-and-host floor plus a 3.98 GB
long-form loaded state does not fit in 4 GB by any reading. Even the 3.11 GB short-workload
figure leaves only ~0.6-0.9 GB for the compositor and browser on a 4 GB T600, whose usable
VRAM is somewhat below 4 GB nominal.

Two caveats that cut in your favour, and one that does not:

- **The 2.37 GB floor is a WSL2 artifact.** Those measurements were taken on "Windows 11 Home
  w/ WSL2" ([README](https://github.com/remsky/Kokoro-FastAPI)). WSL2's GPU paravirtualisation
  and its separate CUDA context are known to inflate both context size and host footprint.
  Native Windows or bare Linux should be materially cheaper. I did **not** find a native-Windows
  or Linux figure for the same setup — so the exact native floor is **unverified**.
- **The same page reports much smaller live footprints.** In the VRAM issue thread,
  `nvidia-smi` showed `/usr/bin/python3 1020MiB`, cut to **800 MiB** with
  `CLEAR_CUDA_CACHE=true` and `N_CACHE_VOICES=1`
  ([#15](https://github.com/remsky/Kokoro-FastAPI/issues/15)); a later user reported
  **1616 MiB** on the same server. So a bare pipeline can sit around 0.8-1.6 GB — the 3.11/3.98 GB
  figures are the full FastAPI server with warmup, voice cache and activation pool, not the model.
- **The "2 GB VRAM" guidance you will see quoted is a requirements recommendation, not a
  measurement — and it conflicts with the one measured floor.** Clore.ai's guide (the primary
  source smeltcore cites) states outright: *"82M parameters... Despite its tiny size (**under 2 GB
  VRAM**)"*, with a requirements table of **GPU "Any with 2 GB VRAM", VRAM minimum 2 GB /
  recommended 4 GB, RAM 4 GB / 8 GB**
  ([Clore.ai](https://docs.clore.ai/guides/audio-and-voice/kokoro-tts.md)). Spheron's blog
  similarly claims "weights under 1GB at FP16, though total GPU memory during inference
  (including CUDA kernels and buffers) runs 2-3GB" — while its own table footer admits "RTF
  figures are estimates based on model architecture and available community benchmarks"
  ([Spheron](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/)).
  smeltcore.com then repeats the 2-3 GB number citing Spheron
  ([smeltcore](https://smeltcore.com/recipes/kokoro-tts-on-rtx-4060-ti-16gb-82m-parameter-text-to-speech-47-voices-under-3-gb-vram/)).

  **These conflict with the only measured figure I found.** Clore says "under 2 GB"; Kokoro-FastAPI
  measured a **2.37 GB floor** *before the model is even usefully loaded*, and 3.11-3.98 GB loaded.
  Both can be partly right: Clore is describing a bare `KPipeline` with fp32 weights and no server,
  while the FastAPI measurement includes warmup, voice cache, activation pool, and WSL2's CUDA
  context. But the practical consequence is that **the widely-quoted "fits in 2 GB" is optimistic
  for anything server-shaped, and on a 4 GB card the difference is decisive.** Marked
  **unverified** in the table above because it is guidance, not a benchmark.

**With batching:** predicted cost goes up, but nobody has measured it. The per-item activation
memory is dominated by the alignment matrix, which in upstream code is built as
`torch.zeros((input_ids.shape[1], indices.shape[0]))` with no batch dimension
([model.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/model.py)) — the
community batch implementation in issue #55 allocates one such matrix *per item* in a Python
loop, i.e. `pred_aln_trg[idx]` for each index
([#55 comment](https://github.com/hexgrad/kokoro/issues/55)). At 510 tokens x ~1500 frames that
is ~765k floats per item in fp32. A batch of 8 therefore adds on the order of tens of MB, not
hundreds — so **batching is unlikely to be the thing that breaks 4 GB**; the CUDA context and
server overhead are. But this is **derived reasoning, not a measurement**, and it is
batch implementation-dependent.

---

## 2. Benchmarked throughput / RTF

### CPU

The cleanest single measurement is the `gauravvij/kokoro-tts-vs-supertonic-3-tts` CPU benchmark
you pointed me at ([repo](https://github.com/gauravvij/kokoro-tts-vs-supertonic-3-tts)). Hardware:
**Intel Xeon Platinum 8272CL, 4 cores, 15.6 GB RAM, no GPU**, Python 3.12, `CUDA_VISIBLE_DEVICES=''`,
150 timed runs across 5 configs.

| Config | Mean RTF (proc/audio) | vs realtime | Mean UTMOS |
|---|---|---|---|
| Kokoro-82M (ONNX) | 0.5711 | **1.8x** | 4.44 |
| Kokoro-82M (PyTorch) | 0.7865 | **1.3x** | 4.45 |
| Supertonic-3 (5-step) | 0.3164 | 3.2x | 4.37 |
| Supertonic-3 (2-step) | 0.1781 | 5.6x | 1.53 |
| Inflect-Nano-v1 | 0.1376 | 7.3x | 3.48 |

Two things worth extracting: ONNX is ~1.38x faster than PyTorch on this CPU, and Kokoro was
**the slowest of the five but the best quality** (tied 4.44/4.45 UTMOS). Note the repo's own
caveat that this is an autonomous-agent-produced benchmark.

The `efemaer` gist is more pessimistic on a bigger CPU: **5x** for both PyTorch and ONNX on a
c32-vCPU EPYC 7R32
([gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653)).

The best-instrumented CPU numbers come from the ICASSP-submission PTQ study, on a Mac mini
M4 Pro with 4 pinned threads, 20 sentences x 5 repeats, with a quiet-host gate
([timing_mini.md](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md)):

| Condition | rtf_median (proc/audio) | ~realtime multiple | peak RSS |
|---|---|---|---|
| `kokoro_fp32` | **0.0692** | ~14.5x | 2712 MB |
| `kokoro_w4` | 0.0695 | ~14.4x | 2724 MB |
| `kokoro_dyn8` (int8 dynamic) | **0.0772** | ~13.0x | 2722 MB |
| `supertonic_fp32_nfe8` | 0.1482 | ~6.7x | 607 MB |

That is a 4-thread number on a fast 2024 ARM core, and it is **not** an x86 measurement — you
asked specifically for modern x86 and the honest answer is that the best x86 numbers available
are the two older ones above (1.3-1.8x on a 4-core Xeon, 5x on a 32-vCPU EPYC). Also note
`kokoro` here reads as ~2x faster than Supertonic whereas the gauravvij benchmark found the
opposite ordering; the hosts, thread counts and harnesses differ, so do not mix them.

Also, for real-world ebook conversion specifically, `audiblez` (the closest published analogue
to your project) reports **~60 chars/s on an M2 MacBook Pro CPU** — about an hour for a
160k-character novel ([audiblez](https://github.com/santinic/audiblez)).

**Practical read for a laptop CPU:** Kokoro is right around realtime on 4 modern cores and
comfortably faster on 8+. For an ebook reader that synthesises a sentence at a time and streams,
CPU is viable. For bulk whole-book conversion it is slow but workable.

### Accelerator backends: TensorRT, `torch.compile`, CoreML

**TensorRT — no published benchmark exists, and this is itself the finding.** I found **zero**
TensorRT latency or throughput numbers for Kokoro-82M anywhere. Two independent conversion
attempts fail:

- The TRT execution provider fails at session init:
  `TensorRT input: /decoder/decoder/generator/istft/stft/Gather_output_0 has no shape specified`
  — open since 2025-01-18 ([kokoro-onnx #50](https://github.com/thewh1teagle/kokoro-onnx/issues/50)).
- A direct `trtexec` run with **TensorRT 8.6.13 on Jetson Orin** fails with
  `Cannot infer squeeze dimensions from a dynamic shape!`, plus unsupported ops `SequenceAt`,
  `SequenceInsert`, `SplitToSequence` ([HF discussion #15](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/discussions/15)).
  The engine never builds.

A Jetson TTS port that converted only *vocoders* to TRT calls Kokoro "hardest (Loop/If + fused
source-filter, no clean boundary)"
([jetson-tts PR #1](https://github.com/vieenrose/jetson-tts/pull/1)) — note that PR's measured
speedups are for **MeloTTS and Matcha, not Kokoro**, so do not attribute them.
**Do not chase TensorRT for this model**; the blocker is structural (data-dependent shapes and
control flow in the StyleTTS2 alignment + iSTFT path), not a missing recipe.

**`torch.compile` — a single 1.34x point, then outright failures.** A one-line fix
(`np.prod()` -> `math.prod()`) makes Kokoro compile-able, and on an RTX 4070 Ti SUPER that took
eager 4.20 ms -> compiled 3.13 ms = **1.34x** ([hexgrad/kokoro PR #91](https://github.com/hexgrad/kokoro/pull/91)),
with the PR candidly noting "still plenty of graph breaks". Subsequently `torch.compile` was
reported to **fail on Kokoro in both modes** — `fullgraph=True` raises a dynamo
`UserError: Could not guard on data-dependent expression`, and `fullgraph=False` raises a Triton
`ValueError: Pointer argument (at 0) cannot be accessed from Triton (cpu tensor?)`
([pytorch#149570](https://github.com/pytorch/pytorch/issues/149570)). Treat 1.34x as an
optimistic single data point, not a dependable gain.

**CoreML / Apple Silicon — the only backend with solid, multiply-reproduced numbers, but the win
is memory, not speed.** On an M4 Pro 48 GB: PyTorch CPU 16.987x, MLX 23.796x, Swift+CoreML
23.225x realtime ([FluidInference card](https://huggingface.co/FluidInference/kokoro-82m-coreml)).
So CoreML is only **~1.37x faster than PyTorch CPU** and a **wash against MLX** (~0.98x). Its real
advantage is footprint: **1.503 GB peak vs PyTorch CPU's 4.85 GB**, plus a ~15 s one-time compile
and ~2 s subsequent loads. Independent ports agree on the 14-27x range: 25.0x mean on an M4 Mac
Mini 24 GB and 16.9x on an iPhone 16 Pro
([laishere](https://github.com/laishere/kokoro-coreml)); 14x on an M1 Mini and ~70x on an M2 Ultra
for 30 s clips ([mattmireles bakeoff](https://github.com/mattmireles/kokoro-coreml/blob/main/README/Notes/bakeoff-results-v2.md)).
The bakeoff also shows **PyTorch MPS OOMing at 15 s and 30 s on a 24 GB M2 Air**. One caution: two
`aoiandroid` HF mirrors are **byte-identical copies of the FluidInference card**, not independent
measurements — do not double-count them. And one unexplained discrepancy exists: 16.9x vs ~12.5x
for the same iPhone 16 Pro across two projects.

For your use case all three are irrelevant — you are on Windows/NVIDIA — but the CoreML result is
useful as an independent confirmation that **Kokoro's ceiling is bounded by the vocoder and the
launch profile**, not by any single vendor's acceleration stack.

### NVIDIA GPUs

Almost all public GPU numbers trace back to one gist
([efemaer](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653)), AWS EC2, ~3,000-word
English text, 5 runs discarding the first as warm-up, RTF defined as **audio length / processing
time (higher is better)**:

| Instance | GPU | PyTorch CPU | ONNX CPU | PyTorch CUDA | ONNX CUDA |
|---|---|---|---|---|---|
| g4dn.xlarge | **T4** | - | - | **36x** | **20x** |
| g5.xlarge | A10G | - | - | 96x | 32x |
| g6.xlarge | L4 | - | - | 81x | 37x |
| c6a.8xlarge | none | 5x | 5x | - | - |

**The T4 row is the most directly relevant data point in this entire report.** The T4 is Turing,
compute capability 7.5 — the *same architecture generation* as the T600. It achieves **36x
realtime** with PyTorch CUDA. The T600 Laptop is a much smaller part than a T4 (14 SMs vs 40,
and a 128-bit bus), so expect substantially less — but the T4 result establishes that the
Turing generation runs Kokoro well in fp32, and that **ONNX CUDA underperforms PyTorch CUDA by
~1.8x on this architecture** (20x vs 36x). That inversion is the opposite of the CPU result and
should shape backend choice on a Turing card.

For a real ebook workload on a T4: `audiblez` converts *Animal Farm* (~160k chars) in **~5
minutes at ~600 chars/s** on a Colab T4 via CUDA ([audiblez](https://github.com/santinic/audiblez)).

TensorRT, `torch.compile`, and CoreML are covered in section 4 and the numbers table. The
summary: **TensorRT has no published Kokoro benchmark and two documented conversion failures**;
`torch.compile` yields a single **1.34x** data point and is known to fail outright in some
configurations ([pytorch#149570](https://github.com/pytorch/pytorch/issues/149570)); CoreML on
Apple Silicon is the only backend with solid, multiply-reproduced numbers, at **14-27x realtime**
([FluidInference](https://huggingface.co/FluidInference/kokoro-82m-coreml),
[laishere](https://github.com/laishere/kokoro-coreml)) — but only ~1.4x faster than PyTorch CPU
and a wash against MLX.

**No T600, GTX 1650, MX-series or Quadro P600 Kokoro benchmark exists.** I searched
specifically for these and found nothing. The nearest small-GPU evidence is the T4 row above
and the RTX 3060 Ti figures in section 5.

### ONNX Runtime CUDA execution provider

The reported speedups **directly contradict each other**, which is itself the finding:

- **RTX 4090**: 100 requests x 8 s audio = 800 s audio; **136 s CPU vs 41 s GPU = 3.32x**
  ([kokoro-onnx #37](https://github.com/thewh1teagle/kokoro-onnx/issues/37)).
- **A100**: *"when I use GPU, it is even slower than using CPU. The GPU utilization is also
  low."* The ORT log in that same report shows why:
  `39 Memcpy nodes are added to the graph main_graph for CUDAExecutionProvider. It might have
  negative impact on performance (including unable to run CUDA graph)`
  ([#112](https://github.com/thewh1teagle/kokoro-onnx/issues/112)).
- **RTX 3060 Ti**: CUDA EP bought ~2% (fp16 0.63 s vs fp16-gpu 0.62 s) while fp32 beat both
  ([#112 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/112)).

The mechanism is plausible and consistent with the T4 result from the gist: Kokoro is small
enough that ORT's graph partitioning, 39 host/device memcpys, CPU-pinned shape operators and
per-call kernel launch overhead can exceed the compute actually saved. On a *Turing* card
specifically, the gist's 20x-vs-36x ONNX/PyTorch gap is direct evidence that ORT CUDA is the
weaker path there. **Benchmark both on your own T600 before committing to either.**

---

## 3. Batching

**Honest finding: batching is not natively supported, and no published throughput multiplier
exists.**

I read the upstream source rather than relying on documentation.

- **`KPipeline.__call__` has no `batch_size` parameter.** Its signature is
  `(text, voice, speed, split_pattern, model)` and its body iterates one segment at a time,
  calling `KPipeline.infer(model, ps, pack, speed)` inside the loop — one forward pass per
  phoneme string ([pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py)).
- **`KModel.forward` hardcodes a batch of one:**
  `input_ids = torch.LongTensor([[0, *input_ids, 0]])`
  ([model.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/model.py)).
- **`forward_with_tokens` looks batch-capable but is not.** It derives `input_lengths` from
  `input_ids.shape[0]`, but then builds the alignment target as
  `pred_aln_trg = torch.zeros((input_ids.shape[1], indices.shape[0])).unsqueeze(0)` — a single
  batch dimension — and computes `pred_dur = torch.round(duration).clamp(min=1).long().squeeze()`,
  which collapses the batch dimension using `indices = torch.repeat_interleave(...)` over a flat
  arange ([model.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/model.py)).
  Feeding it a real batch would silently misalign items, not fail loudly.

So the "Nx faster with batch size 8" claim you were looking for **does not exist in any source
I could find**, and the reason is architectural, not accidental.

**The historical record:**

- Issue [#55, "How to do batching?"](https://github.com/hexgrad/kokoro/issues/55) was opened
  2025-02-05 and is **still open**. The reporter's framing is exactly the GPU-utilisation point:
  *"This works totally fine when dealing with single text-to-audio tasks, which underutilise the
  GPU. Is there a way to parallelise this to squeeze out more performance from GPUs?"* Seven
  comments, no upstream implementation. Workarounds discussed: running multiple `KModel`
  instances and multiplexing them (one user notes "it is so small in size that way several are
  loaded up in the GPU at once"), and a hand-written batch forward pass that loops the alignment
  construction per item ([#55 comment](https://github.com/hexgrad/kokoro/issues/55)).
- PR [#239, "Feat: batch support for onnx and triton compatibility"](https://github.com/hexgrad/kokoro/pull/239)
  **was merged** on 2025-07-26 — but the maintainer's approval message is
  *"Blind approving because only `examples/` is touched"*
  ([#239](https://github.com/hexgrad/kokoro/pull/239)). It added batch-dimension support to the
  *ONNX export script* for Triton serving; it did not add batching to `KPipeline`.
- [nimbleEdge/kokoro](https://github.com/nimbleEdge/kokoro) is a genuine batch implementation
  ("Batch Implementation for Kokoro for enhanced on-device performance") with a
  `forward_with_tokens(input_ids, 1.0, input_lengths)` entry point and `rnn.pad_sequence`
  padding. **It publishes zero performance numbers** — no benchmark, no RTF, no hardware, no
  multiplier. A subagent enumerated the full repo tree to confirm there is no metrics file, not
  merely an absent README section.
- [wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch) reports an absolute number
  — `batch_size=32`, `max_chars=200`, RTX 4090, forward pass **1.6319 s** producing **276.15 s**
  of audio (~169x realtime) — but **no `batch_size=1` baseline**, so it yields no multiplier.
  Its per-stage breakdown is more informative than the headline: the final iSTFT takes
  **1.1648 s of the 1.6319 s (71%)** versus 0.0451 s for source generation.

**What that last number means.** The StyleTTS2 + ISTFTNet architecture's cost is heavily
concentrated in the vocoder/iSTFT tail, which is per-frame work that scales with output *audio*
length rather than with text length. Batching N texts into one forward pass reduces per-call
launch overhead and shares the text-encoder work, but the 71% vocoder share means the achievable
multiplier is bounded well below N. Nobody has published how far below.

**Practical guidance:** if you need throughput, the realistic options are (a) run 2-4 `KPipeline`
instances and multiplex them, as users in #55 do; (b) use Kokoro-FastAPI, which achieves
35-100x realtime on a 4060 Ti via chunking and streaming without batched forward passes
([README](https://github.com/remsky/Kokoro-FastAPI)); or (c) adopt nimbleEdge's fork and
**measure the multiplier yourself**, because you cannot cite one.

---

## 4. Quantization

### What the ONNX repos offer

`onnx-community/Kokoro-82M-v1.0-ONNX` is the canonical export. Its model card lists eight
variants with sizes ([card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)):

| File | Size (MB) | Note |
|---|---|---|
| `model.onnx` | 326 | fp32 |
| `model_fp16.onnx` | 163 | fp16 |
| `model_quantized.onnx` | 92.4 | int8 |
| `model_q8f16.onnx` | 86 | mixed int8/fp16 |
| `model_uint8.onnx` | 177 | "8-bit & mixed precision" |
| `model_uint8f16.onnx` | 114 | mixed |
| `model_q4.onnx` | **305** | "4-bit matmul" — barely smaller than fp32 |
| `model_q4f16.onnx` | 154 | 4-bit matmul + fp16 weights |

Note `model_q4.onnx` at 305 MB: only matmul weights are 4-bit, so embeddings/norms stay wide.
The card also documents the `kokoro-js` dtype options as `"fp32" | "fp16" | "q8" | "q4" | "q4f16"`.
The card gives audio samples per variant but **no speed or latency table** — the "tradeoffs" it
documents are size and subjective audio quality only.

The Rust `kokoro-en` crate gives the only explicit guidance I found
([crate](https://docs.rs/crate/kokoro-en/0.1.2)): `model.onnx` fp32 ~325 MB is
*"Best quality. Recommended for CoreML / CUDA"*; `model_q8f16.onnx` is *"Smaller, fast on CPU"*;
`model_quantized.onnx` ~92 MB int8 is *"Smallest. Some quality loss."* It also states that
**quantized models don't run on CoreML** and the library falls back to CPU. Its `q8f16` size
(~160 MB) conflicts with the model card's 86 MB — an unresolved discrepancy; trust the card.

### Quality tradeoffs: Kokoro quantizes unusually well — but only per-channel

This is the most useful quantization result and it comes from a real study (PTQ across 13 TTS
systems, submitted to ICASSP 2027, FLORES-200 evaluation, whisper-large-v3 scoring, paired
bootstrap CIs over 200 sentences)
([RESULTS.md](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/RESULTS.md)):

| Kokoro condition | dUTMOS vs its own fp32 | WER |
|---|---|---|
| fp32 baseline | — | 0.030 |
| **W4 per-channel** | **-0.07** | 0.030 |
| **W4 group:128** | **-0.04** | 0.029 |
| W4 per-tensor | **-3.15** | 0.261 |
| W6 | -0.003 | — |
| W8 + simulated int8 acts (per-channel) | -0.07 | — |
| W4 + simulated int8 acts (per-channel, g128) | -0.13 | — |

Kokoro at **4-bit weights with per-channel or group-wise scales is essentially lossless**
(-0.04 to -0.07 UTMOS on a 1-5 scale, WER unchanged). This is dramatically better than most
architectures in the same study — StyleTTS 2 loses -0.64 per-channel and Supertonic V3 loses
-2.80. And Kokoro's **per-tensor** 4-bit collapse (-3.15) is the same failure mode as everyone
else's, so the granularity of the scales is what matters.

### But quantization does not buy speed, and int8 is actively harmful

This is the counter-intuitive part, and it is well-evidenced:

- On the same Mac mini M4 Pro CPU, ONNX int8 dynamic (`kokoro_dyn8`) ran at **0.0772** vs fp32's
  **0.0692** — i.e. **int8 was ~11% slower than fp32**, and W4 was identical (0.0695)
  ([timing_mini.md](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_mini.md)).
- On an Intel Core Ultra 7 258V CPU, int8 took **27.4 s** where fp32 took **9.49 s** — **2.9x
  slower** ([kokoro-onnx #112 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/112)).
- On an RTX 3060 Ti, int8 took **9.92 s** where fp16 took **0.63 s** — **~16x slower**
  ([same](https://github.com/thewh1teagle/kokoro-onnx/issues/112)).
- On an M4 Pro, int8 dynamic quant was slower than fp32, and the study's separate
  `timing_cuda_r6k_rtx6000.md` shows `omnivoice_int8dyn_lm_nfe32` at RTF 1.72 vs fp16's 0.14 —
  a similar int8-dynamic collapse on GPU
  ([timing_cuda_r6k_rtx6000.md](https://github.com/uxfacdev/tts-ptq-map/blob/main/experiments/results/timing_cuda_r6k_rtx6000.md)).

The mechanism is the usual one: without int8-optimised kernels and with a model this small, the
quantize/dequantize ops and the loss of vectorisation dominate. **Quantize Kokoro for memory,
never for speed.**

### Does ONNX Runtime CUDA EP work on Turing (cc 7.5)?

**Almost certainly yes, though I could not find an explicit minimum-compute-capability statement
for the CUDA EP.** What is documented is the dependency constraint: ORT's CUDA EP requires a
**cuDNN major version match** — "ONNX Runtime built with cuDNN 8.x is not compatible with
cuDNN 9.x, and vice versa" ([ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)).
That version-matching trap, not the compute capability, is the realistic failure mode. Turing
(7.5) is far above any compute-capability floor that has ever appeared in ORT discussions (the
one relevant issue concerns fp16 on cc < 5.3). The `kokoro-onnx` README flags GPU support and
its issue threads show the problem in practice is **wheel selection, not hardware**: `uv`
silently installs CPU-only `onnxruntime` and overrides `onnxruntime-gpu`
([#37 comment](https://github.com/thewh1teagle/kokoro-onnx/issues/37)), a trap that produced the
tell-tale `Specified provider 'CUDAExecutionProvider' is not in available provider names.
Available providers: 'AzureExecutionProvider, CPUExecutionProvider'`.

The practical Turing caveat is the one from section 2: on the T4 row, **ONNX CUDA was 20x while
PyTorch CUDA was 36x** ([gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653)).
ORT CUDA runs on Turing; on Turing it appears to be the slower of the two CUDA paths.

---

## 5. Turing-specific caveats for fp16

### Correction to the brief's premise: the T600 has no tensor cores

Your question assumed "Turing (cc 7.5) has tensor cores but fp16 support differs from Ampere." The
compute-capability part is right; **the tensor-core part is wrong for this specific GPU**, and it
changes the answer.

The T600 Laptop GPU is a **TU117** die — the GTX 1650-class part, not a TU10x. Two independent
sources:

- NotebookCheck states it outright: *"In contrary to the faster Quadro RTX cards, the T600 do not
  feature raytracing and Tensor cores"*
  ([NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html)).
  The same page lists the T600 Laptop GPU as 896 cores @ 1.4 GHz, 128-bit @ 10000 MHz, 4 GB GDDR6,
  2.5 TFLOPS FP32, 160 GB/s bandwidth.
- Wikipedia's Turing die-comparison table lists **Tensor cores and RT cores as "N/a" for TU116 and
  TU117**, present only on TU102/104/106 — and the article body says the GeForce 16 series "utilizes
  the new Turing design but **lacks the RT and Tensor cores**"
  ([Wikipedia, Turing](https://en.wikipedia.org/wiki/Turing_(microarchitecture))).

Compute capability is still **7.5** ([NVIDIA CUDA GPUs](https://developer.nvidia.com/cuda/gpus)),
and Wikipedia's infobox independently confirms cc 7.5 for Turing.

**Consequence:** there is no tensor-core fp16 path on a T600 to exploit or to worry about. The only
fp16 lever is packed fp16 arithmetic on the CUDA cores. Independently, **Turing has no BF16 at
all** — NVIDIA's feature table lists "Bfloat16-precision floating-point operations" as **No** for
7.x and Yes for 8.x+
([CUDA Programming Guide](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html)).
BF16 and TF32 both arrive with Ampere SM80
([NVIDIA CUTLASS docs](https://mintlify.wiki/NVIDIA/cutlass/concepts/tensor-cores)). And ONNX
Runtime's own fp16 path is gated on the hardware having tensor cores: its docs describe the fp16
convolution speedup as cuDNN choosing "tensor core algorithms for the convolution operations
(**if the hardware supports tensor core operations**)"
([ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)) —
a branch that cannot fire on a T600.

So on a T600 the question is not "will tensor cores help?", it is only "does packed fp16 on CUDA
cores beat fp32, and is it numerically safe?" The evidence below answers both: **no, and no.**

### The real fp16 hazard is in the model, not the GPU

There is a specific, credible report that Kokoro's vocoder **cannot be run in fp16** — and it is
the same underlying issue that the Apple CoreML port independently ran into.

[hexgrad/kokoro PR #353](https://github.com/hexgrad/kokoro/pull/353), "Add opt-in exact
integer-phase harmonic source (fp16-safe, zero phase drift)", opened 2026-07-30, **still open,
unmerged**. It documents that `SineGen._f02sine` accumulates phase with an unbounded float32
`cumsum`, synthesizing each of the 9 harmonics with an independent accumulator. Reported
measurements:

- At F0 = 200 Hz the 9th harmonic's phase reaches **~113,000 rad after 10 s**. The fp16 ULP at
  that magnitude exceeds the per-sample increment, so the sines scramble: **correlation with the
  fp32 render falls to 0.006 within 10 s**.
- On CUDA with the legacy source in half precision (36 s utterance), band energies are corrupted
  by **3.2 / 5.0 / 2.9 dB** across thirds of the utterance.
- The PR states this is *"why half-precision deployments currently pin the decoder to fp32 (see
  e.g. the FluidInference CoreML port notes)."*
- It also documents a separate **-150 sample (-6.25 ms)** excitation delay from the
  downsample/cumsum/upsample round trip.
- Perf: the exact source is ~2x *slower* than `SineGen` on CPU (32 vs 15 ms per 30 s of audio),
  and a fused Triton kernel measures faster on GPU (0.93 vs 1.43 ms per 30 s).

**Corroboration from an independent direction:** the `laishere/kokoro-coreml` port publishes a
compute-placement table in which **Noise and Tail must be fp32 and therefore run off-ANE**,
with sine/cumsum phase correlation degrading **0.94 -> 0.82** in fp16
([laishere/kokoro-coreml](https://github.com/laishere/kokoro-coreml)). Different port, different
hardware vendor, same conclusion: the harmonic source does not survive reduced precision.

**Two further independent fp16 failure modes for this architecture:**
- An OpenVINO/Intel GPU deployment fails outright because `ScatterNDUpdate` "is not supported on
  the GPU plugin for the F16 precision"
  ([Intel KB 000101364](https://www.intel.com/content/www/us/en/support/articles/000101364/software.html)).
- The fp16 ONNX export needs ONNX Runtime's own converter rather than `onnxconverter_common`,
  which leaves "mismatched Cast types around the Loop subgraph" and produces a model that fails to
  load ([kokoro-onnx PR #198](https://github.com/thewh1teagle/kokoro-onnx/pull/198)). The same PR
  reports fp16 quality at **0.999 spectral similarity** vs fp32 and "~4x faster on CPU here" — note
  this does not contradict PR #353, because that export keeps the sensitive graph in fp32.

**How much to trust this.** PR #353 is unmerged, authored by a third party, self-labelled
"Generated with Claude Code", and has **zero review comments** — I am marking its specific
figures **unverified**. But the *direction* is corroborated by two independent projects, and the
underlying claim (unbounded phase accumulator, huge magnitudes, fp16 ULP exhaustion) is
mechanically sound and consistent with the known behavior of this decoder family. The
`litert-community` conversion card independently reports related precision fragility: it had to
run the hn-NSF source STFT host-side because *"its atan2 phase flips at the F0->0 pad boundary
on-device"*, and it unrolls the LSTMs and masks the InstanceNorms specifically to avoid
pad-frame contamination ([litert-community card](https://huggingface.co/litert-community/Kokoro-82M)).

**I found no fp16 bug report specific to Turing or GTX 16-series**, and I searched for it
directly. The fp16 problems above are hardware-agnostic — which makes them worse, not better, for
a T600: they will occur regardless of architecture.

### Is fp16 actually faster on a T600-class GPU? No.

This is the strongest empirical answer, and it points the other way from intuition.

[kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112) — a contributor
benchmarked quantized variants on an **RTX 3060 Ti** over ~22 s of speech:

| Variant | Time (s) |
|---|---|
| int8 | 9.92 |
| fp16 | 0.63 |
| fp16-gpu | 0.62 |
| **fp32** | **0.48** |
| fp32 (v0.19) | 0.87 |

**fp32 was the fastest; fp16 was ~30% slower.** (Caveat: the reporter's labels are ambiguous
about which rows used the CUDA EP versus CPU, since `fp16-gpu` is listed separately from `fp16`;
I report the set as given and do not over-interpret individual rows. Note the RTX 3060 Ti *does*
have tensor cores and is a much faster card than a T600, so this is a generous test of fp16 — and
fp16 still lost.)

More evidence that small-workload fp16 regressions are a general bug class rather than a Turing
quirk: PyTorch's AOTInductor was measured **10x slower at batch size 1 and 2.7x slower at batch
size 2** under FP16 autocast on an A10 (Ampere) — the GEMM kernels ran 1.5 ms in fp32 vs 51 ms in
fp16 ([pytorch#159346](https://github.com/pytorch/pytorch/issues/159346), still open). And the M4
Pro CPU study found ONNX fp16 only ~9% faster than fp32 (8.63 s vs 9.49 s).

### Why: an 82M model is launch-limited, not compute-limited

The framing is standard ML-systems theory: execution time decomposes as
`T = D_vol/BW + O/(R_peak·η_hw) + L_lat`, where `L_lat` "captures kernel launch latency,
synchronization, communication, and software stack inefficiency", and the stated learning
objective is to classify kernels as compute-bound, memory-bound, or **launch-limited**
([Harvard MLSys, Performance Engineering](https://mlsysbook.ai/vol2/performance_engineering/performance_engineering.html)).
That chapter also notes each launch "must traverse the Python GIL and the framework's CPU
dispatcher... spending tens of microseconds in Python dispatch per operation before any GPU work
begins."

Kokoro is a ~82M-parameter model with a decoder (iSTFTNet) that runs many small convolutions and
activation functions over a handful of seconds of audio. Its per-call cost is dominated by
`L_lat`, which **fp16 does nothing to reduce** — it only addresses the compute term that was never
the bottleneck. Halving weight bytes does not help either, because the kernel time is not
bandwidth-bound at these sizes on a 128-bit bus with a warm cache.

The correct lever is the overhead term, not precision: ONNX Runtime's `enable_cuda_graph` exists
precisely "to remove CPU overhead associated with launching CUDA kernels sequentially"
([ORT CUDA EP docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)) —
though the documented constraints (no `If`/`Loop`/`Scan` control flow) mean Kokoro's Loop subgraph
may make it ineligible for the whole model. Batching utterances is the other lever, and section 3
explains why that is also limited.

**A numeric crossover point — how large a model must be before tensor cores/fp16 pay off — does
not exist in any source I could find.** I decline to invent one. The defensible statement is the
qualitative one above plus the empirical fact that at this model size fp32 already won in the one
measured Kokoro GPU A/B.

### The practical recommendation, and one good option I had not considered

On a T600 the fp16 options are:

1. **fp16 *storage* with fp32 *compute*.** This is the option worth testing first. A published
   fp16 ONNX variant casts fp16 initializers to fp32 at point of use: **325 MB -> 162 MB** with
   *"compute is unchanged and audio is bit-for-bit intact"*
   ([soniqo/Kokoro-82M-ONNX commit](https://huggingface.co/soniqo/Kokoro-82M-ONNX/commit/0132fba59f8113ec70c9855266724d41f5cdffc0)).
   You get the memory win with zero precision risk.
2. **Stay fp32 and attack launch overhead** instead — but note this buys little on a 4 GB card
   where, as sections 1 and 6 show, VRAM (not speed) is the binding constraint.
3. **Full fp16 compute — avoid.** It risks the measured SineGen/ISTFTNet corruption above and was
   measurably *slower* than fp32 on a faster Ampere card.

**Net: run fp32.** With 4 GB VRAM and 82M parameters, VRAM is not what limits you, so the fp16
speed argument has to stand on its own — and the available evidence does not support it.

---

## 6. GPU contention on a 4 GB laptop

*This is the area with the least published, quantified guidance. Findings below; I flag clearly
what is documented versus what is folklore.*

### Reconciling the two CUDA-context numbers — and why this is good news

Two figures in this report appear to contradict each other, and resolving them matters a lot for a
4 GB card:

- Kokoro-FastAPI measured a **2.37 GB** "host + CUDA context" floor, on Windows 11 + **WSL2**
  ([README](https://github.com/remsky/Kokoro-FastAPI)).
- The mainstream expectation for CUDA context overhead is **~300-500 MB per process**, and the
  standard VRAM budget taught for local inference is:

  ```
  required = weights_on_disk
           + kv_cache(context_length, num_layers, hidden_dim, dtype)
           + activation_overhead       (~10-20% of weights for batched inference)
           + cuda_context_per_process  (~300-500 MB)
           + safety_buffer             (1-2 GB you do not touch)
  ```
  ([dev.to, pre-flight VRAM check](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2))

**My reading, and it is a reading rather than a measurement:** the 2.37 GB figure is inflated by
WSL2's GPU paravirtualisation and by the full FastAPI server's warmup/voice-cache/activation pool,
not by CUDA context alone. WSL2 independently carries **~1.3 GiB of invisible GPU reserve**
([club-3090 FAQ](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md)). Subtract those
and the CUDA-context component plausibly lands in the 300-500 MB range that matches the mainstream
figure.

**If that reading is right, the arithmetic on a native-Windows 4 GB T600 looks much better than the
2.37 GB number suggests:** roughly 300-500 MB CUDA context + 326 MB fp32 weights + activation
overhead + a ~1 GB safety buffer lands at **~1.5-2 GB total**, inside 4 GB with room for the
compositor. That is a materially more optimistic conclusion than "tight to infeasible", and it is
the single most decision-relevant uncertainty in this report.

**But nobody has measured it.** I could not find a native-Windows or native-Linux Kokoro VRAM floor,
so this remains **unverified inference** built from two adjacent sources. The practical implication
for you: it is worth *measuring* rather than assuming. Load `KPipeline` on the bare T600 with no
desktop apps competing, read `nvidia-smi`, then add your browser and video and read it again. That
single five-minute experiment resolves the biggest open question here, and no published source can
answer it for you.

Note also the second bucket in that budget model, which is directly about your question: *"The
display server and other tenants. On a workstation, the desktop compositor sits on the same card.
Browsers with hardware acceleration drift up and down by a couple of GB depending on what you have
open. That number you saw in `nvidia-smi` was a moment ago"*
([same](https://dev.to/casteldazur/how-i-stopped-gguf-models-from-crashing-my-gpu-a-pre-flight-vram-check-44i2)).
On a 4 GB card, "a couple of GB" is most of the card.

### What VRAM exhaustion actually does — NVIDIA's own answer, and it is silent

This is the single most actionable finding for a 4 GB card, and it is official.

NVIDIA's knowledge base documents **System Memory Fallback**, introduced in driver **536.40**:
*"applications which previously crashed when running out of GPU memory ... continue to run, albeit
at lower speeds."* The switch happens "when running close to maxing out GPU memory to allow for a
seamless transition" ([NVIDIA KB 5490, via Wayback](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490)).

Driver **546.01** added the off switch: NVIDIA Control Panel → Manage 3D settings →
**CUDA - Sysmem Fallback Policy → Prefer No Sysmem Fallback**, with the trade-off stated as
"performance stable at the risk of a crash" ([same](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490)).

**NVIDIA publishes no number for the penalty** — only "at lower speeds". That absence is worth
noting because third-party measurements are dramatic and the mechanism makes them credible:
because `cudaMalloc` keeps returning success, **the spill is silent** — nothing raises, and no CUDA
attribute, NVML field or `nvidia-smi` query reports it, while throughput falls "10x to 100x" with a
clean log ([unsloth commit](https://github.com/unslothai/unsloth/commit/3c69ecaa6add633a40ba923df9f56899aa3ff61c));
a measured single-user report shows 18.0 tok/s → 0.9 tok/s (**~20x**) once layers spill
([ollama#16020](https://github.com/ollama/ollama/issues/16020)). It is not binary — only the
spilled fraction crosses PCIe — but I found no partial-spill benchmark on a 4 GB card.

**Consequence for you:** on a 4 GB T600, exceeding VRAM will most likely **not** crash and **not**
produce an error message. Your ebook reader will simply get mysteriously slow — plausibly 10-20x —
while `nvidia-smi` looks fine and the logs are clean. Two responses: (1) set **Prefer No Sysmem
Fallback** so you get a visible OOM you can act on instead of silent degradation; (2) instrument
your own throughput so a regression is visible. This failure mode is far more likely to bite you
than any TDR or hard crash.

### Mode switches can destroy the CUDA context — a documented desktop-contention mechanism

This is the first-party answer to "what happens to the desktop when a CUDA context exists on a
4 GB card", and the causality runs the opposite way from what you might expect.

The CUDA C++ Programming Guide (§6.5, "Mode Switches") states that GPUs with a display output
"dedicate some DRAM memory to the so-called **primary surface**", and that on a mode switch the
system "may have to **cannibalize memory allocations dedicated to CUDA applications**". Critically:
*"a mode switch results in any call to the CUDA runtime to fail and return an **invalid context
error**"* ([CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)).
Mode switches include resolution or bit-depth changes, launching a full-screen DirectX
application, Alt+Tab from one, and a Ctrl+Alt+Del lock.

**Consequence for you:** on a 4 GB card already near its ceiling, ordinary desktop activity —
a video going fullscreen, a display mode change, locking the workstation — can invalidate your CUDA
context and make the next kernel launch fail with an invalid-context error. That is a documented,
first-party mechanism, unlike the TDR folklore. The robust design is to **treat context loss as
recoverable**: catch the invalid-context error, tear down and re-create the pipeline, and keep the
CPU path as a fallback. This also argues for *not* holding a long-lived CUDA context on a 4 GB
desktop card at all — which is exactly what Kokoro-FastAPI's `/dev/unload` pattern achieves.

### TDR: the risk is lower than the local-LLM community implies

NVIDIA's own guidance matches Microsoft's: Nsight's documentation gives the reset condition as no
response "within a certain amount of time (default is **2 seconds**)", and advises *"Disabling TDR
removes a valuable layer of protection, so it is generally recommended that you keep it enabled"*,
suggesting a 10 s delay rather than disabling it. It also confirms the aftermath: after a TDR "the
application will receive a grid launch failure, and the `CUcontext` will begin to report errors"
([Nsight VSE docs](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm)).

But two first-party facts substantially defuse this on a T600:

- **The 2-second wall does not apply to long kernels on modern Windows.** NVIDIA's GTC 2019 talk
  "Using CUDA on Windows" states *"A kernel can now run for more than 2s on WDDM2 without hitting
  a TDR"* — limited to Windows 10 RS4+ and requiring a Pascal-or-newer card, and "enabled by
  default when the configuration supports it"
  ([NVIDIA GTC 2019 S9957](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf)).
  A T600 Laptop is Turing, i.e. newer than Pascal.
- **Preemption works between graphics and compute.** The same talk states long kernels "are now
  preemptible so the graphics apps will stay responsive", and that this "works between processes
  (Graphics / Compute)". Compute preemption is supported from Pascal onward (cc 6+) at
  instruction-level granularity and is automatically enabled where supported
  ([CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)).

NVIDIA does caveat that "just because you can doesn't mean you should run kernels for an extended
period" and that preemption on WDDM is hard to exploit deliberately, because it occurs at internal
WDDM submission boundaries ([same GTC talk](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf)).

**Assessment (inference, not measurement): Kokoro's millisecond-scale kernels are far from the
multi-second regime where TDR risk lives.** The historical freeze reports I found all involve
kernels running longer than the 2 s window. For your workload TDR is close to a non-issue; the
silent sysmem fallback and the invalid-context-on-mode-switch are the real hazards.

### TCC mode is not an escape hatch

The documented alternative to WDDM scheduling is TCC, but it is unavailable and self-defeating
here. NVIDIA's Windows installation guide states that TCC is "available for non-display devices such
as NVIDIA Tesla GPUs and the GeForce GTX Titan GPUs", that "when TCC mode is enabled for a
particular GPU, that GPU cannot be used as a display device", and that "**NVIDIA GeForce GPUs
(excluding GeForce GTX Titan GPUs) do not support TCC mode**"
([CUDA Installation Guide for Windows](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html)).
The CUDA Programming Guide adds that TCC applies to "devices of the Tesla and Quadro Series" and
that "TCC mode removes support for any graphics functionality"
([CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)).

The T600 is Quadro-branded, so TCC is *plausible* in principle — but **whether the Laptop SKU
actually accepts it is unverified**, and on a single-GPU laptop it is moot: enabling TCC would
remove the display you are trying to keep responsive. Check locally with `nvidia-smi -q` if curious;
do not design around it.

### Adjacent evidence of display stutter under GPU load

There is no report for 4 GB-class laptops, but the mechanism is corroborated elsewhere at a
consistent (and higher) scale:

- An ONNX Runtime user running inference on their **primary display GPU** under Windows/WDDM
  reported GUI stuttering with kernels of only **30-50 ms**
  ([NVIDIA forums #382941](https://forums.developer.nvidia.com/t/general-question-should-models-be-structured-to-fit-under-frame-budgets-when-running-ml-work-on-display-gpu/382941)).
  This is the closest analogue to your workload and it is the least reassuring datapoint I found —
  though the "expected behaviour / use prioritized streams + HAGS" reply came from a **forum member,
  not NVIDIA staff**, so the explanation is unverified.
- An RTX PRO 5000 Blackwell user reported displays freezing for the *entire duration* of a
  saturating workload — but only after ~24 h+ uptime, with **no Xid and no TDR logged**, and NVIDIA
  staff opened a bug. That is a scheduler/preemption degradation, not a VRAM-capacity issue
  ([NVIDIA forums #376563](https://forums.developer.nvidia.com/t/rtx-pro-5000-blackwell-windows-displays-freeze-for-the-whole-duration-of-gpu-saturating-work-only-after-24h-uptime-fresh-boot-immune-only-reb/376563)).
- Older reports involve kernels of ~1 s or more on a display GPU: desktop "a bit unresponsive",
  window minimise/maximise taking "a second or two", cursor stutter
  ([NVIDIA forums #9718](https://forums.developer.nvidia.com/t/effect-of-cuda-on-primary-display-device-slow-does-of-desktop-with-some-code/9718));
  and a system freeze with >2 s compute on the display GPU that did not reproduce on a secondary GPU
  ([NVIDIA forums #63834](https://forums.developer.nvidia.com/t/cuda-accelerated-program-running-on-display-gpu-freezes-system/63834)).
- On the AMD side, an RX 7600 XT user saw "significant display stuttering and occasional graphical
  artifacts" once *combined* VRAM usage (including the compositor) passed ~90%
  ([ollama#10229](https://github.com/ollama/ollama/issues/10229)). Notably, that reporter says the
  same overload does **not** reproduce on their NVIDIA RTX A4000 — one of the few
  NVIDIA-favourable data points in this section.

For choppy audio specifically (relevant to a reader that plays TTS while you browse): a Microsoft
Q&A thread shows a latency profile where `dxgkrnl.sys` max ISR was **72,906 µs** and max DPC
**73,794 µs**, with buffer underruns "appearing as drop outs, clicks or pops" — but the cause was
never established and no CUDA load was involved
([Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5534656/experiencing-audio-and-video-stuttering-from-dxgkr)).
The pattern is at least consistent: Graphics Kernel interrupts blocking long enough to underrun an
audio buffer. I found **no evidence connecting xrun/JACK failures to CUDA compute load.**

My own caveat on T600 bandwidth: sources conflict — one third-party database lists **192 GB/s**
([CpuTronic](https://cputronic.com/gpu/nvidia-t600-mobile)) while NotebookCheck's spec implies
**~160 GB/s** (128-bit @ 10000 MHz, [NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU-GPU-Benchmarks-and-Specs.532552.0.html)).
I therefore do not compute a spill-bandwidth ratio; treat the figure as uncertain.

### The quantified overheads that exist

- **Windows/WDDM reserves VRAM that `nvidia-smi` does not show.** Quantified at **~1.3 GiB of
  "invisible GPU overhead"** — "the Windows display driver, CUDA runtime, and WDDM reserve VRAM
  that `nvidia-smi` doesn't report at idle but is locked once a container starts. On a 24 GB card
  that leaves you with ~22.7 GB usable instead of 24 GB"
  ([club-3090 FAQ](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md)). This is a
  community source, and it is about LLMs on 3090s, not TTS — but the WDDM reserve mechanism is
  model-agnostic and it is the only *quantified* figure I found. **Treat as indicative, not
  authoritative.**
- **A desktop session measurably costs VRAM.** The same source: the headless-tuned
  `--gpu-memory-utilization` defaults "assume a headless rig with ≥23.3 GiB consistently free. If
  you're running a desktop session on the same card, **0.92-0.94** is the safer ceiling"
  ([same](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md)). That is roughly
  **1.1-1.6 GB consumed by a desktop session** on a 24 GB card — on a 4 GB card, proportionally
  less in absolute terms but a much larger *share*. It also reports that a 4090 "carries more idle
  desktop + driver VRAM than a headless 3090, so single-card context ceilings land ~15-20% lower."
- **The compositor, browser GPU acceleration and stray processes are named as the usual holders
  of VRAM.** The FAQ's diagnosis for "free memory ... is less than desired GPU memory utilization"
  is explicit: *"If something else on the GPU is holding memory (X11 / Wayland compositor,
  leftover container, Python process, **browser GPU acceleration**), the check fails"*
  ([same](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md)). This is the closest
  thing to published guidance on your exact contention question, and it is an enumeration of
  culprits rather than a benchmark.
- **TDR: the default is 2 seconds, and it is a real reset mechanism.** Microsoft's official
  registry reference: `TdrDelay` "Specifies the number of seconds that the GPU can delay the
  preempt request from the GPU scheduler... **The default value is 2 seconds**", with
  `TdrLevel` defaulting to `TdrLevelRecover` (recover on timeout) and `TdrDdiDelay` defaulting to
  5 s. Repeated TDRs are not free either: `TdrLimitTime` defaults to 60 s and `TdrLimitCount` to
  **5**, after which the OS bug-checks with `VIDEO_TDR_FAILURE` (0x116)
  ([Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys)).
  So a TDR manifests as a display driver reset — which is exactly the visible symptom you would
  describe as the desktop glitching out — and enough of them crash the machine.

  **Two important qualifications.** First, Microsoft explicitly discourages the widely-circulated
  fix: *"End users shouldn't manipulate these registry keys. Applications shouldn't manipulate
  these registry keys outside of targeted testing or debugging during driver development."* The
  community advice to extend `TdrDelay` to 60 s
  ([club-3090 FAQ](https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md)) therefore
  contradicts Microsoft's own guidance and should be treated as an unsupported workaround.
  Second, **TDR fires on preempt delays, which are driven by long-running kernels.** The reports
  that trigger it are long-context LLM prefills holding the GPU for seconds at a time. Kokoro's
  kernels are small and short — an 82M model doing many brief launches, not one multi-second
  kernel. **My assessment, stated as inference rather than measurement: TDR risk for Kokoro
  specifically is low, and the TDR anxiety in the local-LLM community does not transfer to this
  workload.** There is no Kokoro TDR report because there is no plausible mechanism for one.

**What is otherwise documented:**

- **A CUDA context on a display-driving GPU is not free, and on this stack it was large.**
  Kokoro-FastAPI measured a **2.37 GB floor** described as "host + CUDA context" on
  Windows 11 + WSL2 ([README](https://github.com/remsky/Kokoro-FastAPI)). Regardless of what
  fraction is context versus host, that number is the difference between "fits in 4 GB" and
  "does not". On a native Linux or native Windows install the context overhead is normally much
  smaller, but **I found no native-Windows or Linux measurement of Kokoro's floor**, so the
  native figure is **unverified**.
- **VRAM is spiky, not flat.** Two independent reports: the Kokoro-FastAPI maintainer notes
  "It will still have a spike of reserved memory during inference as is"
  ([#15](https://github.com/remsky/Kokoro-FastAPI/issues/15)), and a kokoro-onnx user on a
  24 GB RTX 4090 filed a bug titled *"the GPU memory usage sometimes spikes unexpectedly"*
  ([kokoro-onnx #37](https://github.com/thewh1teagle/kokoro-onnx/issues/37)). Peak, not mean,
  is what OOMs you.
- **The mitigation exists and is documented:** Kokoro-FastAPI's `POST /dev/unload` releases the
  model from VRAM and reloads lazily, reclaiming 758 MiB (short) to 1,656 MiB (long-form) at a
  cost of ~5 s reload, plus `CLEAR_CUDA_CACHE=true` and `MODEL_AUTO_UNLOAD_TIMEOUT_SECONDS`.
  Users confirm `CLEAR_CUDA_CACHE` cut usage from 1020 MiB to 800 MiB. **For a 4 GB card this is
  the single most valuable operational lever**, because it means the CUDA context can be
  transient rather than resident.
- **Driver-wheel and provider selection is the most common real failure**, ahead of any hardware
  limit: `uv`/pip silently installing CPU-only `onnxruntime` over `onnxruntime-gpu`, and the
  cuDNN major-version match requirement
  ([ORT docs](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html),
  [kokoro-onnx #37](https://github.com/thewh1teagle/kokoro-onnx/issues/37)).

**What I could not verify — and this is the honest state of the evidence for your question:**

- I found **no NVIDIA or Microsoft guidance** on running a small CUDA inference workload alongside
  a desktop compositor, browser and video playback. Microsoft documents TDR mechanics (above) but
  not this coexistence scenario. The compositor/browser-as-VRAM-holders insight is a community
  FAQ, not vendor guidance.
- I found **no quantified reports of desktop stutter caused by CUDA memory pressure on 4 GB-class
  laptops** — nothing for T600, MX-series, GTX 1050/1650, or Quadro P600.
- I found **no TDR report attributed to Kokoro or any small TTS model**. (See the reasoning above
  for why I expect that absence to be real rather than under-reporting.)
- **The T600-specific contention and stutter evidence base is effectively empty.** Treat any
  confident claim you encounter about it — including the inference below — as reasoning from
  adjacent evidence, not as a measured result.

**What follows from the verified numbers, stated as inference:**

The arithmetic on a 4 GB T600 is uncomfortable. If the CUDA-side footprint really is ~2.4-3.1 GB
(WSL2 floor and short-workload figures), and the Windows compositor plus a browser with video
already consumes several hundred MB of the same 4 GB, the headroom is thin or negative. Two
consequences worth planning for: the desktop will contend for VRAM allocation and for SM time
shares; and once the driver begins spilling to shared system memory across PCIe, latency
degrades by orders of magnitude rather than gracefully. **On a 4 GB card the sane configuration
is CPU inference**, which the CPU numbers in section 2 show is approximately realtime on modern
cores. The GPU is worth engaging only if you unload it between utterances (`/dev/unload`
pattern), pin a single cached voice, keep the model resident but the activation pool small, and
accept that the desktop owns the card the rest of the time.

---

## 7. Streaming vs batch output quality

**Yes — chunking demonstrably causes artifacts, and there is a documented tension between chunk
size and quality that cuts both ways.**

### The mechanics

- **Hard limit: 510 phoneme tokens.** `context_length` is 512 (BERT `max_position_embeddings`
  minus BOS/EOS). `KPipeline.en_tokenize` splits when `next_pcount > 510`, and
  `generate_from_tokens` raises `ValueError(f'Phoneme string too long: {len(tokens)} > 510')`
  ([pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py)).
- **Default `split_pattern=r'\n+'`** — paragraph-splitting only. `KPipeline.__call__` does
  `text = re.split(split_pattern, text.strip())` and yields one `Result` per segment, so with the
  default a whole paragraph becomes one chunk, and *within* a segment a second chunking pass
  applies.
- **Boundary placement is a heuristic, `waterfall_last`**, which walks a priority list
  `['!.?…', ':;', ',—']` backwards from the end of the buffer looking for a punctuation token
  such that the remaining text still fits under 510 phonemes, with a "bump" adjustment for
  closing quotes/parens ([pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py)).
  So Kokoro *does* try to split at sentence boundaries first and fall back to clause-level
  punctuation. It is not naive character chopping.
- **Non-English is worse.** For `es`/`fr`/`hi`/`it`/`pt` via espeak, the pipeline logs a warning:
  *"Chunking logic not yet implemented, so long texts may be truncated unless you split them with
  '\n'."* Those languages then use a separate ~400-character sentence-boundary chunker with a
  hard character-slice fallback ([pipeline.py](https://raw.githubusercontent.com/hexgrad/kokoro/main/kokoro/pipeline.py)).

### Evidence of artifacts at boundaries

- **Kokoro-FastAPI, self-reported, both directions:** *"The model takes up to 510 phonemized
  tokens per chunk, but running it that long tends to produce 'rushed' speech and other
  artifacts. The server adds its own chunking layer on top"* — with defaults
  `TARGET_MIN_TOKENS=175`, `TARGET_MAX_TOKENS=250`, `ABSOLUTE_MAX_TOKENS=450`. And the opposite
  failure: *"Artifacts in intonation can increase with smaller chunks"*
  ([README](https://github.com/remsky/Kokoro-FastAPI)). **So there is a quality sweet spot in the
  middle** — around 175-250 phoneme tokens — and both extremes degrade. This is the most
  actionable finding for your reader: do not naively maximise chunk size, and do not split at
  every comma.
- **Abbreviation/period ambiguity causes misplaced splits and long unwanted pauses.**
  [Kokoro-FastAPI #308](https://github.com/remsky/Kokoro-FastAPI/issues/308): *"Kokoro is
  chunking text in a wrong place at times when there are shorts perhaps as it does not see the
  difference between full stop period and the period after abbreviation. It causes unwanted long
  pause in the middle of some sentences."* The reporter's logs show a split landing mid-sentence
  after "(e.g., inner harbor..." producing chunk 1 then a truncated chunk 2. The same issue
  documents missing pauses after "etc.", no pause/handling around parentheses, and no
  intonation rise before "e.g.,".
- **End-of-generation clicks in a sibling implementation.**
  [TTS.cpp #39](https://github.com/mmwillet/TTS.cpp/issues/39), titled *"Kokoro implementation
  clicks at the end of its generation"* — "Kokoro output will always click at termination." The
  author first suspected their linear-interpolation upscaler, ruled it out, and **closed the
  issue without publishing a root cause** (with `1` comment). Treat as a real observed artifact
  with an unknown cause; note this is a C++ port, not the PyTorch `kokoro` package, so it may not
  affect you.
- **Related in the same family:** a Swift port carries a PR literally titled *"Trim Kokoro
  trailing artifacts"* ([speech-swift #235](https://github.com/soniqo/speech-swift/pull/235)),
  and the `litert-community` conversion had to move the hn-NSF source STFT host-side because
  *"its atan2 phase flips at the F0->0 pad boundary"* — i.e. **the model emits start/end
  transients that require explicit trimming or padding handling**
  ([litert-community](https://huggingface.co/litert-community/Kokoro-82M)).
- **Long-text correctness bug:** [hexgrad/kokoro #42](https://github.com/hexgrad/kokoro/issues/42)
  reports that on very long inputs (~40k chars, a book chapter) the `[word](/ipa/)` pronunciation
  hint is *ignored* and both parts are spoken — reproducible only on very long text, workaround
  is manual splitting. Closed by the maintainer.

### Practical implications for an ebook reader

1. **Split at paragraph (`\n\n`) then at sentence boundaries; never at commas.** The default
   `split_pattern=r'\n+'` is already paragraph-oriented, but a paragraph longer than 510
   phonemes will still be internally split by `waterfall_last`, which will fall through to `:;`
   and then `,`. Raising the split granularity yourself at sentence level gives you control.
2. **Aim for ~175-250 phoneme tokens per chunk**, per Kokoro-FastAPI's tuned defaults — large
   enough to avoid small-chunk intonation artifacts, small enough to avoid "rushed" speech.
3. **Protect abbreviations.** `etc.`, `e.g.`, `i.e.`, `Mr.`, `Dr.`, and decimals are the
   documented cause of mid-sentence splits. Pre-normalise them (e.g. to `etc\u2009` or expanding)
   before phonemisation if you see long mid-sentence pauses.
4. **Do not chase low latency by shrinking chunks.** The neosun100 project drove first-audio
   latency from 2 s to ~100 ms purely by splitting at `[.!?,;:]+` instead of `\n+`
   ([STREAMING_OPTIMIZATION.md](https://github.com/neosun100/kokoro-tts/blob/main/docs/STREAMING_OPTIMIZATION.md)) —
   that is a real latency win, but it is exactly the small-chunk regime Kokoro-FastAPI warns
   increases intonation artifacts. Trading prosody for TTFB is a deliberate choice.
5. **Cross-fade or trim chunk joins.** The click/trailing-artifact reports suggest chunk
   boundaries emit transients; a short fade or a few-millisecond trim at each join is cheap
   insurance, and your existing concatenation path is the place to add it.

---

## 8. Explicit gaps — things that do not exist

So the parent agent does not chase these:

1. **No Kokoro benchmark on a T600, GTX 1650, MX-series, or any 4 GB laptop GPU.** The T4 row is
   the closest same-architecture proxy (also Turing, cc 7.5).
2. **No published batch-N-vs-batch-1 throughput multiplier for any Kokoro implementation.**
3. **No published TensorRT benchmark for Kokoro** — and two documented conversion failures.
4. **No documented minimum compute capability for the ORT CUDA EP**; only the cuDNN
   major-version constraint (cc 7.5 *is* in the official build arch list, `75-real`).
5. **No native-Windows or native-Linux measurement of Kokoro's CUDA-context floor** (the 2.37 GB
   figure is WSL2). This is the highest-value unknown in this report.
6. **No report — quantified or otherwise — of desktop stutter on MX150/MX250/GTX 1050 2 GB/T600/
   Quadro P600/GTX 1650 under CUDA or local-model load.** I searched for this specifically and
   found nothing citable. Stated as a gap, not dressed up as a null result.
7. **No NVIDIA- or Microsoft-published number for the sysmem-fallback performance penalty** — only
   "at lower speeds". Third-party figures (10-100x, ~20x) are third-party.
8. **No Microsoft documentation of WDDM scheduling between a CUDA compute context and the DWM
   compositor.** The current CUDA C++ Programming Guide contains *no* TDR content at all; NVIDIA
   documents TDR only in the Nsight guide and forums.
9. **No TTS-specific guidance** from any vendor for coexisting with desktop workloads.
10. **No link between xrun/JACK audio failures and CUDA compute load** — no evidence found either way.
11. **No verified T600 Laptop bandwidth**: sources conflict at 192 GB/s vs ~160 GB/s. NVIDIA's
    datasheet 404s and the third-party databases bot-block.
12. **Whether the T600 *Laptop* SKU accepts TCC mode** is unverified.
13. **Conflicting `q8f16` model size** (86 MB on the model card vs ~160 MB in the Rust crate).
14. **No citable FLOP threshold** for when tensor cores or fp16 start to pay off — I decline to
    invent one.
15. **The PTQ study's own CUDA timing tables are labelled "INCOMPLETE - DO NOT CITE"** by the
    authors (GPU idle floor drifted 103-336%); I cited only the CPU table and used the CUDA table
    solely as a qualitative int8-vs-fp16 contrast, flagged as such.

---

## 9. Bottom line

- **The brief's fp16 premise needs correcting first.** The T600 Laptop GPU is a **TU117** die with
  **no tensor cores at all** (verified from two sources). So there is no tensor-core fp16 path on
  this GPU, and Turing has no BF16 either. The fp16 question reduces to "does packed fp16 on CUDA
  cores help?" — and the measured answer is no.
- **VRAM is the real risk on a 4 GB T600 — but the pessimism may be a WSL2 artifact.** The 82M
  weights are trivial (163-326 MB), but the surrounding footprint is not. Measured on Windows 11 +
  **WSL2**: a **2.37 GB "host + CUDA context" floor**, 3.11 GB loaded (short), 3.98 GB loaded
  (long-form). **However**, the mainstream CUDA-context figure is **~300-500 MB per process**, and
  WSL2 independently carries ~1.3 GiB of invisible reserve — so the 2.37 GB is very likely inflated
  by WSL2 plus the FastAPI server's warmup/voice cache. If so, a native-Windows T600 lands around
  **1.5-2 GB total**, which fits 4 GB with room for the compositor. **Nobody has measured this.**
  It is a five-minute experiment worth running: load `KPipeline` on the bare card, read
  `nvidia-smi`, then add your browser and video and read it again. That resolves the biggest open
  question in this report, and no published source can answer it for you.
- **Mitigation is documented and matters more than backend choice:** `POST /dev/unload` reclaims
  758 MiB-1.66 GB for ~5 s reload, and `CLEAR_CUDA_CACHE=true` cut a live pipeline from 1020 MiB to
  800 MiB. On a 4 GB card, making the CUDA context transient rather than resident is the whole game.
- **CPU is the safe default.** ~1.3-1.8x realtime on a 4-core Xeon, ~5x on 32 vCPUs, ~14.5x on
  4 M4 Pro threads, ~60 chars/s for a real book on an M2. Adequate for streaming sentence-by-
  sentence; slow for whole-book batch.
- **GPU works and is much faster, in fp32.** The same-generation Turing T4 hits **36x realtime with
  PyTorch CUDA**. The T600 has roughly a third of the T4's SMs and less bandwidth, so expect well
  below 36x — but still far ahead of CPU.
- **Use PyTorch, not ONNX CUDA, on Turing.** The T4 row shows ONNX CUDA at 20x vs PyTorch CUDA at
  36x — a 1.8x inversion that also matches the A100 "GPU slower than CPU" report and the
  RTX 3060 Ti "CUDA EP bought 2%" report. Benchmark both, but start with PyTorch.
- **Run fp32; if you want the memory win, use fp16 *storage* + fp32 *compute*.** fp16 compute was
  measured ~30% *slower* than fp32 on an RTX 3060 Ti (a faster card with tensor cores), and the
  decoder has a documented (if unmerged) fp16 phase-accumulation failure with independent
  corroboration from the CoreML port. The fp16-storage trick halves weights 325 -> 162 MB with
  bit-identical audio and zero precision risk.
- **Do not quantize for speed; quantize for memory.** W4 per-channel is near-lossless quality-wise
  (-0.04 to -0.07 UTMOS vs a 4.52 baseline) but gives no speedup; int8 was 3-16x *slower* in every
  measurement found. Note `model_q4.onnx` is 305 MB, barely smaller than fp32.
- **Batching is not natively supported** and there is no multiplier to cite. The alignment target
  tensor in upstream `model.py` is hardcoded to batch 1, and `forward_with_tokens` would silently
  misalign a real batch. If you need throughput, use chunking + streaming (Kokoro-FastAPI reaches
  35-100x on a 4060 Ti) or multiplex multiple pipelines.
- **Chunking artifacts are real and there is a sweet spot** of roughly 175-250 phoneme tokens.
  Both 510-token chunks ("rushed" speech) and very small chunks (intonation artifacts) degrade
  output. Guard abbreviations to avoid mid-sentence splits.
- **The GPU-contention worry is real but takes a different form than expected.** Three specifics:
  - **VRAM exhaustion is silent, not fatal.** NVIDIA's sysmem fallback (driver 536.40+) means you
    will *not* get a crash — you get "lower speeds", with third-party measurements at 10-100x
    slowdown and **clean logs**. Set **Prefer No Sysmem Fallback** so you get a visible OOM, and
    instrument your own throughput. This is the failure mode most likely to affect you.
  - **Desktop activity can invalidate your CUDA context.** Mode switches (resolution change,
    fullscreen DirectX, Alt+Tab, Ctrl+Alt+Del lock) can make the driver "cannibalize" CUDA
    allocations and cause subsequent CUDA calls to fail with an **invalid context error** (CUDA
    Programming Guide §6.5). Treat context loss as recoverable: catch it, rebuild the pipeline, keep
    the CPU path alive.
  - **TDR is close to a non-issue here.** NVIDIA's own GTC materials state a kernel can exceed 2 s
    on WDDM2 without a TDR (Windows 10 RS4+, Pascal or newer), and that preemption works between
    graphics and compute so graphics apps stay responsive. Kokoro's millisecond-scale kernels are
    nowhere near the regime where TDR reports occur. Microsoft also advises against the registry fix
    the community usually recommends.
- **The honest headline on contention evidence: there is none for your hardware class.** I found
  **no report at all** — quantified or otherwise — of desktop stutter on a 4 GB-class laptop
  (MX150/MX250/GTX 1050 2 GB/T600/P600/GTX 1650) under CUDA load. The nearest analogue, an
  ONNX Runtime user with 30-50 ms kernels on a display GPU reporting GUI stutter, is the least
  reassuring datapoint found and should temper optimism slightly.
- **What does not exist and should not be claimed:** any T600/small-GPU Kokoro benchmark, any
  batch-N multiplier, any TensorRT benchmark, any documented ORT CUDA minimum compute capability,
  any native-Windows Kokoro VRAM floor, any NVIDIA-published sysmem-fallback penalty, and any
  quantified small-GPU desktop-stutter report.
