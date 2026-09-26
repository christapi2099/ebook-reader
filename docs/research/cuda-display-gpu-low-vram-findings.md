# CUDA on a display GPU, low VRAM (4 GB) — web research findings

Scope: running a small CUDA workload (Kokoro-82M TTS, ~80 M params) on the **same** NVIDIA GPU
that drives the Windows desktop compositor, a browser and video playback, on a 4 GB laptop GPU
(NVIDIA T600 Laptop). Question: desktop stutter / instability risk.

Method: web search + direct fetch of primary docs. Every claim below carries a URL.
Items I could **not** verify are marked `unverified` or `no evidence found` — no numbers invented.

---

## 1. Numbers table

| Claim | Value | Source URL | Status |
|---|---|---|---|
| TDR `TdrDelay` default (GPU preempt-wait timeout) | 2 seconds | [MS TDR Registry Keys](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| Default Windows TDR timeout period | 2 seconds | [MS WDDM TDR support](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/timeout-detection-and-recovery) | verified |
| Default TDR timeout (NVIDIA's own wording) | 2 seconds | [NVIDIA Nsight VSE 2.2 — TDR](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm) | verified |
| TDR `TdrDdiDelay` default → VIDEO_TDR_FAILURE (0x116) bugcheck | 5 seconds | [MS TDR Registry Keys](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| `TdrLimitTime` / `TdrLimitCount` defaults | 60 s / 5 TDRs | [MS TDR Registry Keys](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/tdr-registry-keys) | verified |
| TCC mode on Windows is available for which GPU series | "devices of the Tesla and Quadro Series"; "TCC mode removes support for any graphics functionality" | [CUDA C++ Programming Guide §6.6](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified (text), applicability to T600 Laptop `unverified` |
| GeForce (non-Titan) GPUs and TCC | "NVIDIA GeForce GPUs (excluding GeForce GTX Titan GPUs) do not support TCC mode" | [CUDA Installation Guide for Windows §3.5](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html) | verified |
| Compute preemption hardware requirement | Pascal onwards, compute capability major revision 6 and higher; enabled automatically | [CUDA C++ Programming Guide, Programming Interface](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| Compute preemption effect on long kernels | "can be prevented from either monopolizing the system or timing out"; has context-switch overhead | [CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| NVIDIA: kernel can exceed the 2 s TDR window on WDDM2 | "A kernel can now run for more than 2s on WDDM2 without hitting a TDR" — Windows 10 RS4+, requires a Pascal card | [NVIDIA GTC 2019 S9957, *Using CUDA on Windows* (PDF)](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) | verified |
| Compute preemption is cross-process (graphics ↔ compute) | "Works between processes (Graphics / Compute)"; long kernels "are now preemptible so the graphics apps will stay responsive" | [NVIDIA GTC 2019 S9957 (PDF)](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) | verified |
| NVIDIA caveat on long kernels even with preemption | "Just because you can doesn't mean you should run kernels for an extended period" | [NVIDIA GTC 2019 S9957 (PDF)](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) | verified |
| Mode switch → CUDA failure | "a mode switch results in any call to the CUDA runtime to fail and return an invalid context error"; triggers include launching a full-screen DX app, Alt+Tab out of one, Ctrl+Alt+Del | [CUDA C++ Programming Guide §6.5 Mode Switches](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| Display-attached GPU must dedicate DRAM to the primary surface | Yes — "GPUs that have a display output dedicate some DRAM memory to the so-called primary surface" | [CUDA C++ Programming Guide §6.5](https://docs.nvidia.com/cuda/cuda-c-programming-guide/) | verified |
| NVIDIA shared-memory fallback exists (Windows) | Introduced in driver **536.40**; applications that would have crashed "continue to run, albeit at lower speeds" | [NVIDIA KB 5490 (archived)](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | verified |
| Fallback is switchable off | Driver **546.01+**: NVIDIA Control Panel → Manage 3D Settings → **CUDA - Sysmem Fallback Policy → Prefer No Sysmem Fallback** | [NVIDIA KB 5490 (archived)](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | verified |
| NVIDIA's quantified penalty for spill | **Not given** — KB says only "at lower speeds" | [NVIDIA KB 5490 (archived)](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490) | verified absence |
| Windows will let a GPU adapter consume system RAM | `SharedSystemMemory` = "the maximum value of system memory that may be consumed by the adapter during operation" | [MS DXGI_ADAPTER_DESC](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc) | verified |
| WDDM has a system-memory segment for GPU allocations | Yes — "system memory segment", `SegmentId==0`, reached via the aperture segment | [MS GPU Segments](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-segments) | verified |
| Measured spill penalty (LLM, Windows) | 18.0 tok/s resident → **0.9 tok/s** spilled (≈20×) | [ollama#16020](https://github.com/ollama/ollama/issues/16020) | reported by one user, not independently reproduced |
| Spill penalty as claimed by a shipping project | "silently runs 10x to 100x slow with a clean log" | [unsloth commit 3c69eca](https://github.com/unslothai/unsloth/commit/3c69ecaa6add633a40ba923df9f56899aa3ff61c) | project engineering claim, `unverified` |
| Spill penalty (secondary blog) | "5–10× slowdown"; ~30× PCIe-vs-VRAM bandwidth gap | [runaihome blog](https://runaihome.com/blog/shared-gpu-memory-slow-local-ai-sysmem-fallback-fix-2026/) | `unverified` (secondary, content-marketing site) |
| NVIDIA T600 Laptop GPU VRAM / bus | 4 GB GDDR6, 128-bit | [CpuTronic T600 Mobile](https://cputronic.com/gpu/nvidia-t600-mobile) | third-party, `unverified` |
| T600 Laptop memory bandwidth | **Conflicting**: 192 GB/s (CpuTronic) vs "128 Bit @ 10000 MHz" ≈ 160 GB/s (Notebookcheck snippet) | [CpuTronic](https://cputronic.com/gpu/nvidia-t600-mobile) · [Notebookcheck comparison](https://www.notebookcheck.net/GeForce-GTX-1050-Desktop-vs-T600-Laptop-GPU-vs-GeForce-GTX-1060-Mobile_7583_10685_7362.247598.0.html) | `unverified` — sources disagree; do not quote a single figure |
| Kokoro-82M VRAM footprint | weights <1 GB FP16; total inference footprint **2–3 GB** (CUDA kernels + buffers) | [Spheron TTS deployment guide](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/) | third-party vendor blog, `unverified` |
| Kokoro-82M real-time factor | RTF ≈ 0.03 on an A100 | [Spheron TTS deployment guide](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/) | third-party, `unverified` |
| Kokoro-onnx GPU memory can spike unexpectedly during inference | Open bug report, GPU = RTX 4090 24 GB | [kokoro-onnx#37](https://github.com/thewh1teagle/kokoro-onnx/issues/37) | reported, unresolved |
| Windows compositor/GPU-driver DPC latency coinciding with AV stutter | max ISR 72,906 µs (`dxgkrnl.sys`); max DPC 73,794 µs (`Wdf01000.sys`) | [MS Q&A 5534656](https://learn.microsoft.com/en-us/answers/questions/5534656/experiencing-audio-and-video-stuttering-from-dxgkr) | single user's LatencyMon log, cause not established |
| Audio dropouts on an NVIDIA-driven DAW | performance spikes, playback freeze, distortion then total dropout, one monitor black | [Steinberg forum thread](https://forums.steinberg.net/t/major-problems-with-nvidia/951066) | community report, not vendor-confirmed |
| Quantified stutter on 4 GB-class NVIDIA laptops under CUDA load | — | — | **no evidence found** |

---

## 2. What NVIDIA actually says about CUDA + display/compositor (item a)

**There is first-party NVIDIA material, but no single "do not do this" guide.** Four separate
primary sources add up to a coherent picture:

1. **Driver model.** Windows offers WDDM (display devices) and TCC (non-display devices, "NVIDIA
   Tesla GPUs and the GeForce GTX Titan GPUs"). Enabling TCC means the GPU **cannot** be used as a
   display device, and GeForce GPUs other than Titan do not support TCC at all.
   — [CUDA Installation Guide for Windows §3.5](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html)
   The Programming Guide adds that TCC applies to "devices of the Tesla and Quadro Series" and
   "removes support for any graphics functionality".
   — [CUDA C++ Programming Guide §6.6](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)
   *Practical read:* TCC is the only documented way to escape WDDM scheduling, and it is
   self-defeating on a single-GPU laptop — it removes the display. Whether a T600 **Laptop** SKU
   accepts TCC is `unverified`; check locally with `nvidia-smi -q` / `nvidia-smi -dm 1` rather than
   assuming from the "Quadro series" wording.

2. **Compute preemption is the mechanism that makes coexistence safe.** Long-running kernels on a
   display GPU used to block GUI updates outright (see §5). Modern NVIDIA hardware preempts compute
   at instruction-level granularity: "applications with long-running kernels can be prevented from
   either monopolizing the system or timing out", automatically enabled on supporting devices,
   queryable via `cudaDevAttrComputePreemptionSupported`, at the cost of context-switch overhead.
   — [CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)

3. **NVIDIA's own GTC 2019 talk states the 2 s TDR barrier is gone on WDDM2** — "A kernel can now
   run for more than 2s on WDDM2 without hitting a TDR", limited to Windows 10 RS4+ and requiring a
   Pascal-class card. It explicitly says preemption "Works between processes (Graphics / Compute)"
   and that long kernels "are now preemptible so the graphics apps will stay responsive". It also
   warns: "Just because you can doesn't mean you should run kernels for an extended period", and
   notes preemption happens at "internal WDDM submission boundaries".
   — [NVIDIA GTC 2019 S9957, *Using CUDA on Windows* — Raphael Boissel (PDF)](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf)

4. **The display costs VRAM by design, and desktop events can kill the CUDA context.** "GPUs that
   have a display output dedicate some DRAM memory to the so-called primary surface, which is used
   to refresh the display device". Increasing primary-surface demand (resolution/bit-depth change,
   launching a full-screen DirectX app, Alt+Tab away from one, Ctrl+Alt+Del lock) "may have to
   cannibalize memory allocations dedicated to CUDA applications" — and "a mode switch results in
   any call to the CUDA runtime to fail and return an invalid context error".
   — [CUDA C++ Programming Guide §6.5 Mode Switches](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)

**Gap (stated honestly):** I found **no Microsoft documentation** that describes how the WDDM GPU
scheduler arbitrates between a CUDA compute context and the DWM compositor specifically. Microsoft
documents the scheduler only in the TDR and GPU-segment contexts
([MS WDDM TDR support](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/timeout-detection-and-recovery),
[MS GPU Segments](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-segments)).
There is also **no TDR section in the current CUDA C++ Programming Guide** (I grepped the
Release 13.4 PDF for `TDR` / `watchdog` — zero hits); TDR is documented by NVIDIA only in the
[Nsight VSE user guide](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm)
and on the developer forums.

---

## 3. What happens on Windows with a CUDA context on the display GPU (item a, part 2)

- A CUDA context on a display GPU is an ordinary valid configuration under WDDM. Nothing is
  "unsupported" — but the context is vulnerable to display-side events (§2.4 above).
- After a TDR, the CUDA context is destroyed: "the application will receive a grid launch failure,
  and the `CUcontext` will begin to report errors"
  — [NVIDIA Nsight VSE 2.2 — TDR](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm)
- TDR recovery is largely seamless but not free: GPU state is reset, the video memory manager
  "purges all allocations from video memory", the user sees a screen flicker and the message
  "Display driver stopped responding and has recovered", and "some legacy DirectX applications
  might just render black at the end of this recovery, which requires the end user to restart these
  applications". Well-written D3D apps must release and recreate their device.
  — [MS WDDM TDR support](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/timeout-detection-and-recovery)
- NVIDIA's Nsight guidance explicitly warns against disabling TDR ("Disabling TDR removes a
  valuable layer of protection, so it is generally recommended that you keep it enabled") and
  recommends raising the delay to **10 s** rather than disabling it, for single-GPU local debugging.
  — [NVIDIA Nsight VSE 2.2 — TDR](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm)
- **A lower-end GPU is the specific hazard:** NVIDIA staff (Cliff Woolley) note the timeout problem
  "is especially true when you have a kernel that might run in far less than two seconds on a
  higher-end GPU but that takes longer on a lower-end GPU", and that a batch of short kernels
  submitted together must *all* complete inside one timeout period.
  — [NVIDIA Developer Forums thread 15056](https://forums.developer.nvidia.com/t/display-driver-stopped-responding-and-has-recovered-wddm-timeout-detection-and-recovery/15056)

**Are there reports of CUDA compute causing display driver crashes or black screens? Yes.**
- A user reported "a brief blackout followed by 'Display stopped responding and has recovered'"
  when growing kernel size on a GTX 480, with `TdrLevel`/`TdrDelay` edits not helping.
  — [NVIDIA Developer Forums thread 18176](https://forums.developer.nvidia.com/t/cuda-kernel-execution-timeout-on-geforce-trying-to-turn-off-the-kernel-timeout-on-gtx480-for-compute/18176)
- 2018 report: >2 s of compute on the display GPU made the system freeze and never complete; the
  same code on a non-display GPU ran for 6+ hours unaffected.
  — [NVIDIA Developer Forums thread 63834](https://forums.developer.nvidia.com/t/cuda-accelerated-program-running-on-display-gpu-freezes-system/63834)
- Same thread family contains many "nvidia display drivers have stopped responding" / black-screen
  reports, though those are not all CUDA-attributable.
  — [NVIDIA Developer Forums thread 15056](https://forums.developer.nvidia.com/t/display-driver-stopped-responding-and-has-recovered-wddm-timeout-detection-and-recovery/15056)

*Caveat:* these reported crashes are from 2010–2018 and involve kernels running **longer than the
2 s window**. They do not describe a small, millisecond-scale workload like Kokoro inference.

---

## 4. VRAM exhaustion and shared-memory fallback (item b) — verified, with a real penalty

**Yes, Windows/NVIDIA spills to system RAM, and it is official.**

- NVIDIA implemented it in driver **536.40**: an application that exhausts GPU memory now "use[s]
  shared memory", so apps "which previously crashed when running out of GPU memory ... continue to
  run, **albeit at lower speeds**". The switch happens "when running close to maxing out GPU memory
  to allow for a seamless transition". Driver **546.01+** added the opt-out,
  **CUDA - Sysmem Fallback Policy → Prefer No Sysmem Fallback**, whose trade-off NVIDIA states as
  "performance stable at the risk of a crash".
  — [NVIDIA KB 5490, "System Memory Fallback for Stable Diffusion" (archived)](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490)
- Windows-side, this is the `SharedSystemMemory` budget: "the maximum value of system memory that
  may be consumed by the adapter during operation".
  — [MS DXGI_ADAPTER_DESC](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc)
  WDDM's memory model has an explicit "system memory segment" reachable via the aperture segment.
  — [MS GPU Segments](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-segments)

**Penalty — NVIDIA publishes NO number.** KB 5490 says only "at lower speeds". Everything numeric
below is third-party:

| Source | Claim | Quality |
|---|---|---|
| [ollama#16020](https://github.com/ollama/ollama/issues/16020) | 18.0 tok/s → **0.9 tok/s** (≈20×) when layers spill; 2× RTX PRO 4500 Blackwell, Windows 11 | one user, measured, reproducible-looking detail |
| [unsloth commit 3c69eca](https://github.com/unslothai/unsloth/commit/3c69ecaa6add633a40ba923df9f56899aa3ff61c) | spill is silent ("cudaMalloc returns success, nothing raises, and no CUDA attribute, NVML field or nvidia-smi query reports that it happened") and runs "**10x to 100x slow**" | project engineering claim, not benchmarked in the commit |
| [runaihome blog](https://runaihome.com/blog/shared-gpu-memory-slow-local-ai-sysmem-fallback-fix-2026/) | "5–10× slowdown"; 1,008 GB/s GDDR6X vs ~32 GB/s PCIe 4.0 x16 ≈ 30× bandwidth gap | secondary marketing blog citing TechPowerUp; `unverified` |
| [ollama#10229](https://github.com/ollama/ollama/issues/10229) | AMD ROCm/Ubuntu: >90% combined VRAM (incl. compositor) → display stuttering + artifacts, 1.8 → 0.4 tok/s | AMD, not NVIDIA — but same shape |

**Important nuance for a 4 GB card:** the slowdown is not binary. Only the *spilled fraction* is
served over PCIe, so the penalty scales with how far past VRAM you go — that is the argument made by
the secondary sources, and it is consistent with the mechanism (weights/KV re-read every token).
I did **not** find a benchmark of partial spill on a 4 GB card.

**T600 Laptop bandwidth:** third-party sources disagree (192 GB/s vs ~160 GB/s — see table), so I
will not compute or quote a spill-ratio figure. `unverified`.

---

## 5. Real user reports of desktop stutter (item c)

**Direct answer: no quantified report found for 4 GB-class NVIDIA laptops.**
I searched for MX150 / MX250 / GTX 1050 2 GB / T600 / Quadro P600 / GTX 1650 + desktop
stutter/lag/UI freeze under CUDA or local-model load. **Nothing citable surfaced.** This is a gap,
not a null result I can dress up.

What I *did* verify, on adjacent hardware/platforms — all consistent in direction:

| Report | Platform | Finding |
|---|---|---|
| [NVIDIA forums 9718](https://forums.developer.nvidia.com/t/effect-of-cuda-on-primary-display-device-slow-does-of-desktop-with-some-code/9718) | GTX 280 ×2, Windows XP, kernels "just short of a second" | "If I run the code on my primary display device, Windows becomes a bit unresponsive... Maximising and minimising of windows take a second or two, and sometimes the cursor stutters." No problem on a non-display GPU. NVIDIA forum answer: "CUDA gives complete control of the GPU over to your kernel for the second it is running... Kernel calls on the order of a second will introduce noticeable lag. If you can shorten them to 100 ms, things might be tolerable." |
| [NVIDIA forums 63834](https://forums.developer.nvidia.com/t/cuda-accelerated-program-running-on-display-gpu-freezes-system/63834) | Titan X Pascal, Win10 | >2 s compute on the display GPU froze the system; fine on the secondary GPU |
| [NVIDIA forums 382941](https://forums.developer.nvidia.com/t/general-question-should-models-be-structured-to-fit-under-frame-budgets-when-running-ml-work-on-display-gpu/382941) | ONNX Runtime on the primary display GPU, Windows/WDDM | kernels taking 30–50 ms → GUI stutters; answer given by a **forum member, not NVIDIA staff**: preemption can't instantly halt a kernel, suggests separate prioritized streams, TensorRT EP, HAGS. "Expected behavior" is the forum member's assertion — treat as `unverified` |
| [NVIDIA forums 376563](https://forums.developer.nvidia.com/t/rtx-pro-5000-blackwell-windows-displays-freeze-for-the-whole-duration-of-gpu-saturating-work-only-after-24h-uptime-fresh-boot-immune-only-reb/376563) | RTX PRO 5000 Blackwell, Windows 11, 48 GB | displays freeze for the **entire duration** of a saturating workload, but only after ~24 h+ uptime; ETW shows display work never serviced while the compute queue is saturated; no Xid/TDR logged. NVIDIA staff opened a bug. Not a VRAM-capacity issue — a preemption/scheduler degradation issue |
| [llama.cpp #11793](https://github.com/ggml-org/llama.cpp/discussions/11793) | Radeon 3400G iGPU, Linux X11/Wayland, Vulkan | "the whole desktop GUI ... tends to freeze completely"; higher batch → stuttering; "full offloading makes the GUI unusable, freezing for 2-3 seconds at a time"; VAE phase froze the UI for its whole 20–120 s run. Mostly resolved by turning off `enforce_isolation` on kernel 6.12. **Linux/Vulkan/iGPU — not Windows/NVIDIA**, but the same physics |
| [ollama#10229](https://github.com/ollama/ollama/issues/10229) | AMD RX 7600 XT, ROCm, Ubuntu | at >~90% combined VRAM "significant display stuttering and occasional graphical artifacts", 1.8 → 0.4 tok/s. The reporter explicitly notes this **does not** happen on their RTX A4000 under similar load |

**Community mitigation guidance** (secondary, but the only how-to I found):
[devnen WINDOWS_VRAM_HEADLESS.md](https://raw.githubusercontent.com/devnen/qwen3.6-windows-server/refs/tags/v1.3.4/docs/WINDOWS_VRAM_HEADLESS.md)
and [OnlyTerp/windows-is-fine-for-llms](https://github.com/OnlyTerp/windows-is-fine-for-llms)
— boot the model first then reopen apps; disable browser/Teams/Discord hardware acceleration;
prefer `Prefer No Sysmem Fallback` + a clear OOM over silent paging; raise `TdrDelay`. Both documents
are explicit about which claims are community knowledge rather than vendor documentation.
Their VRAM-budget tables (DWM 0.4–1.2 GB at 1080p–4K SDR, Chrome +0.3–0.7 GB, etc.) are
community estimates — `unverified`; measure locally with `nvidia-smi` instead.

---

## 6. TTS on GPU alongside desktop work; audio glitching (item d)

**No guidance specific to TTS + desktop coexistence found** — from NVIDIA, Microsoft or an
engineering blog. Absent.

Related verifiable material:

- **Kokoro's own footprint is small but not negligible on 4 GB.** Third-party: weights <1 GB FP16,
  total inference footprint 2–3 GB including CUDA kernels/buffers, RTF ≈ 0.03 on an A100
  ([Spheron](https://www.spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026/), `unverified`).
  Independent corroboration that it can be much smaller in practice: a Kokoro-FastAPI user measured
  **1020 MiB** VRAM for the serving process
  ([Kokoro-FastAPI#15](https://github.com/remsky/Kokoro-FastAPI/issues/15)); an ONNX variant claims
  <512 MB ([kokoro-lite-railway](https://github.com/bon5co/kokoro-lite-railway), `unverified`).
  There is also an **open, unresolved** report of GPU memory **spiking unexpectedly during
  inference** on a 24 GB card ([kokoro-onnx#37](https://github.com/thewh1teagle/kokoro-onnx/issues/37)).
  *Implication:* on a 4 GB card shared with the desktop, headroom is the binding constraint, and
  transient spikes are the specific thing to watch — not steady-state weights.
- **Audio glitching under GPU load is real but unquantified.**
  - A user's LatencyMon log alongside simultaneous audio+video stutter shows max ISR **72,906 µs**
    (`dxgkrnl.sys`, DirectX Graphics Kernel) and max DPC **73,794 µs** (`Wdf01000.sys`), with the
    tool concluding buffer underruns "appearing as drop outs, clicks or pops". **The cause was never
    established** in the thread, and no CUDA/compute load was involved.
    — [MS Q&A 5534656](https://learn.microsoft.com/en-us/answers/questions/5534656/experiencing-audio-and-video-stuttering-from-dxgkr)
  - Pro-audio community: an RTX 4070 Ti user reported Cubase performance-meter spikes, playback
    freezing, then distortion and total audio dropout plus a monitor going black; the accepted fix
    was forcing the DAW onto the iGPU or using NVIDIA Studio drivers rather than the gaming
    driver/app. Multiple users corroborate driver-version sensitivity via LatencyMon.
    — [Steinberg forum](https://forums.steinberg.net/t/major-problems-with-nvidia/951066)
  - TTS-specific: RealtimeTTS issue #272 reports choppy, gappy synthesized audio with the
    CUDA/Coqui engine on an RTX 4060. **Open, no root cause identified** — it cannot be attributed
    to GPU contention from the thread.
    — [RealtimeTTS#272](https://github.com/KoljaB/RealtimeTTS/issues/272)
- **`xrun` / JACK:** I found **no** source connecting xrun-style real-time audio failures to CUDA
  compute load. `no evidence found`.

---

## 7. Bottom line for the Kokoro-on-T600-Laptop question

What the evidence actually supports:

1. **It is a supported, normal configuration.** CUDA on a WDDM display GPU is what NVIDIA designs
   for; compute preemption (Pascal+, i.e. this Turing card) exists specifically so long kernels do
   not monopolise the GPU, and NVIDIA's GTC 2019 talk says the 2 s TDR wall no longer applies there.
2. **The stutter risk is not primarily VRAM volume — it is (a) memory pressure triggering silent
   sysmem spill and (b) any kernel/operation that occupies the GPU long enough to starve the
   compositor.** A Kokoro-class workload is milliseconds-scale, so the TDR/black-screen class of
   failure reported in §3 is unlikely; the realistic risks are spill-driven slowdown and compositor
   micro-stutter if the GPU is saturated.
3. **Concrete, evidence-backed levers:** keep VRAM headroom so spill never triggers
   ([NVIDIA KB 5490](https://web.archive.org/web/2024/https://nvidia.custhelp.com/app/answers/detail/a_id/5490));
   consider `Prefer No Sysmem Fallback` so failure is a visible OOM rather than a crawl; leave
   `TdrDelay` at default or raise it modestly rather than disabling TDR
   ([NVIDIA Nsight guidance](https://developer.nvidia.com/w/NsightVisualStudio/2.2/Documentation/UserGuide/HTML/Content/Timeout_Detection_Recovery.htm));
   expect a CUDA context loss on display mode switches / full-screen DX launches / Alt+Tab
   ([CUDA Programming Guide §6.5](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)).
4. **What is genuinely unknown:** no quantified stutter measurement exists for this hardware class,
   and NVIDIA publishes no number for the spill penalty. Any specific stutter figure for a
   T600 + Kokoro setup would have to be measured locally (`nvidia-smi`, LatencyMon, frame-time
   capture) — it cannot be cited.

## 8. Explicit gaps / "no evidence found"

- No Microsoft doc on WDDM scheduling between CUDA compute and the compositor.
- No TDR documentation in the current CUDA C++ Programming Guide.
- No first-party NVIDIA or Microsoft *quantified* sysmem-fallback penalty.
- **No report at all** of desktop stutter on MX150 / MX250 / GTX 1050 2 GB / T600 / Quadro P600 /
  GTX 1650 under CUDA or LLM load — quantified or otherwise.
- No TTS-specific guidance for coexisting with desktop workloads.
- No source linking xrun / JACK real-time audio failures to CUDA compute load.
- T600 Laptop VRAM bandwidth: sources conflict (192 vs ~160 GB/s) — not quotable.
- Whether T600 Laptop accepts TCC mode: not verified.
