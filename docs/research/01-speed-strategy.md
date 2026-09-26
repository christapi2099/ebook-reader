# 01 · Playback speed strategy: native Kokoro `speed=1.5` vs synthesise-at-1.0-then-time-stretch

**Researched:** on branch `main` at `e3938d1` ("fix(backend): close voice path traversal and stop MP3
export blocking the loop") **plus uncommitted working-tree changes** — at the time of writing
`backend/services/tts_engine.py` was being rewritten (a `_effective_speed()`/`_synthesis_pool`
revision, `git diff` = +145/−43), `backend/services/modal_remote.py` is new and untracked, and
`frontend/src/lib/components/AudioProgressBar.svelte` had just been rewritten mid-session. Where a
line number is quoted for those three files it was re-checked against the working tree at the end of
this research; everything about the *Kokoro package itself* is pinned to a version, not to the tree.

**Environment:** interpreter `backend/venv/bin/python` (Python 3.12.3, `kokoro` **0.9.4**,
`misaki` 0.9.4, `torch` 2.14.0, `soundfile` 0.14.0, `scipy` 1.18.1, numpy 2.5.3). A second,
uv-managed `backend/.venv` also exists; its `kokoro` is **also 0.9.4** and its `model.py:108` is
byte-identical, so the central finding is not interpreter-dependent. `librosa` is **not installed in
either venv**.

**Hardware caveat, read this before quoting any timing:** the host has an NVIDIA T600 Laptop GPU,
but `/dev/nvidia*` is not exposed in this sandbox and `torch.cuda.is_available()` is `False`. **Every
number below is a CPU (sandbox) measurement, or a count (frames/samples) that is hardware-independent.**
Frame and sample counts are exact and reproducible; wall-clock and CPU-second figures from this box
are not (see Evidence 21).

---

## Question

When a reader chooses 1.5× or 2.0×, is it better to (A) synthesise at 1.0× with Kokoro and then
time-stretch/resample the resulting audio to the target rate, or (B) ask Kokoro for the rate
directly with `speed=1.5`? Answering that requires knowing what Kokoro's `speed` argument actually
does in the installed version, whether the app's 0.5–3.0 UI range is real at the top, how cost and
quality differ between the two strategies, whether word-level timestamps survive a strategy switch,
and what this repo currently does (including its duplicated synthesis paths).

---

## TL;DR / Recommendation

**Keep strategy B — native `speed=` — and do not build a time-stretcher for the 1.5×/2.0× case.**
Kokoro 0.9.4 does not resample: `speed` divides the *predicted phoneme durations before* the
alignment matrix and vocoder run (`kokoro/model.py:108`), so a higher speed genuinely synthesises
fewer audio frames, cheaply and with pitch intact (measured F0 ratio 1.5×: **0.976**, i.e. −0.42
semitones; a naive resample of the same take lands at **1.488**, i.e. +6.9 semitones — the chipmunk
effect). Native `speed` also keeps word timestamps correct for free, because `join_timestamps()`
counts the *already divided* frame counts, which is what `tts_engine` consumes. Strategy A would
mean adding a real time-stretch (WSOLA or phase vocoder — `librosa` is not installed and plain
resampling is disqualified) plus rescaling every timestamp and the first-chunk latency budget, in
exchange for a quality gain that **this research did not measure**. Two separate real problems were
found, and neither is an argument for A: the UI's **3.0× is not deliverable** (a hard one-frame-per-
token floor caps the achieved rate at ≈2.15×, and at 1.5× **71%** of tokens already sit at that 25 ms
floor), and the backend does not validate `speed`, so `speed=0` or `NaN` from any client raises
inside synthesis.

---

## Evidence

### What `speed` does inside the installed version

**1. `speed` scales the predicted durations *before* frames exist. — Verified-by-code.**
`backend/venv/lib/python3.12/site-packages/kokoro/model.py:107-119` (`KModel.forward_with_tokens`):

```python
107:        duration = self.predictor.duration_proj(x)
108:        duration = torch.sigmoid(duration).sum(axis=-1) / speed
109:        pred_dur = torch.round(duration).clamp(min=1).long().squeeze()
110:        indices = torch.repeat_interleave(torch.arange(input_ids.shape[1], device=self.device), pred_dur)
111:        pred_aln_trg = torch.zeros((input_ids.shape[1], indices.shape[0]), device=self.device)
112:        pred_aln_trg[indices, torch.arange(indices.shape[0])] = 1
...
114:        en = d.transpose(-1, -2) @ pred_aln_trg
115:        F0_pred, N_pred = self.predictor.F0Ntrain(en, s)
117:        asr = t_en @ pred_aln_trg
118:        audio = self.decoder(asr, F0_pred, N_pred, ref_s[:, :128]).squeeze()
```

This is answer **(i)** from the question: the duration/prosody predictor's output is divided by
`speed`, rounded, floored at 1 frame per token, and then expanded into the alignment matrix
`pred_aln_trg`. The expansion width *is* the number of acoustic frames, so the vocoder
(`self.decoder`, an iSTFTNet in `kokoro/istftnet.py`) is fed **`sum(pred_dur)` frames** — strictly
fewer at higher speed. There is **no** post-hoc resample anywhere in the package.

**2. No resampling, no interpolation, no final `torchaudio`/`soundfile` call anywhere in the package. — Verified-by-code.**
`grep -rn "speed" backend/venv/lib/python3.12/site-packages/kokoro/*.py` returns only: `model.py`
91/108/125/133/148/150, `pipeline.py` 228–232/238/246/266/279/355/383/431, `__main__.py` 40/47/51/59/104/107/143.
`speed` is passed straight through `KPipeline.infer` → `model(ps, pack[len(ps)-1], speed, return_output=True)`
(`pipeline.py:228-232`). Nothing in `istftnet.py` or `custom_stft.py` sees `speed`.

**3. Because the duration is divided before frame generation, audio duration is exactly `frames × 25 ms`. — Measured.**
24000 Hz ÷ 40 frames/s = 600 samples/frame. Every measurement in the run below satisfies
`samples == frames × 600` exactly (e.g. 176 frames → 105 600 samples → 4.400 s). This is a
hardware-independent confirmation of the code path: had `speed` been implemented as a resample,
sample counts would have been produced at 1.0× density and then decimated.

**4. Word timestamps are already speed-adjusted, by construction and by measurement. — Verified-by-code + Measured.**
`pipeline.py:284-320` `join_timestamps()` walks `pred_dur` (the *post-division* vector) and converts
half-frames to seconds with `MAGIC_DIVISOR = 80` (`pipeline.py:286-289`: "Multiply by 600 to go from
pred_dur frames to sample_rate 24000 … We will count nice round half-frames, so the divisor is 80").
Measured on the same sentence, the last word `riverbank` ends at **4.150 s** (speed 1.0),
**2.750 s** (1.5), **2.300 s** (2.0), **1.900 s** (3.0) — ratios 1.509 / 1.804 / 2.184, i.e. the
timestamps track the frame counts, and in each case the last word ends *before* the audio ends
(slack 250/200/175/150 ms).

**5. The supported `speed` range is not clamped anywhere in kokoro 0.9.4. — Verified-by-code + Measured.**
There is no validation of the argument. What *is* clamped is the per-token duration
(`model.py:109`, `clamp(min=1)`), and that is the only effective ceiling. Measured behaviour
(`docs/research/01-speed-strategy-range.py`, same 12-word sentence, 70 tokens):

| requested `speed` | result |
|---|---|
| 0.25 / 0.5 / 3.0 / 4.0 / 6.0 | OK — 712 / 356 / 82 / 77 / 72 frames (17.80 / 8.90 / 2.050 / 1.925 / 1.800 s) |
| `0.0` | **RuntimeError: repeats can not be negative** |
| `NaN` | **RuntimeError: repeats can not be negative** |
| `-1.0` | OK — 70 frames (1.750 s), i.e. everything clamped to one frame |
| `inf` | OK — 70 frames (1.750 s) |

70 frames is the absolute floor for this sentence: one frame per token (70 tokens × 25 ms = 1.75 s).

**6. The achieved rate saturates well below the requested rate, because of `clamp(min=1)`. — Measured + Verified-by-code.**
The analytic model `sum_i max(1, round(d_i / speed))`, built from the measured per-token duration
vector at 1.0×, predicts the measured frame totals to within 0–4 frames (0–4%) at every speed
(`docs/research/01-speed-strategy-durationmodel.py`). Effective speed-up = `frames(1.0) / frames(s)`:

| requested | frames (short sent.) | effective | frames (long sent.) | effective |
|---|---|---|---|---|
| 0.5 | 356 | 0.494× | 1029 | 0.497× |
| 0.75 | 240 | 0.733× | 698 | 0.732× |
| 1.0 | 176 | 1.000× | 511 | 1.000× |
| 1.25 | 148 | 1.189× | 433 | 1.180× |
| **1.5** | **118** | **1.492×** | **329** | **1.553×** |
| **2.0** | **99** | **1.778×** | **284** | **1.799×** |
| **3.0** | **82** | **2.146×** | **238** | **2.147×** |

So the 3.0× button delivers ≈2.15×, and 2.0× delivers ≈1.78–1.80×. The short-sentence ceiling,
measured directly, is 2.514× (`speed=inf` → 70 frames). **The top of the app's 0.5–3.0 range is a
real argument value but not a real playback rate.**

**7. The floor is not a corner case: most tokens are pinned to it in the upper half of the range. — Measured.**
Of the 70 tokens in the test sentence, the number rendered at the 1-frame (25 ms) floor is **24/70
(34%) at 1.0×**, **50/70 (71%) at 1.5×**, **64/70 (91%) at 3.0×**, 70/70 at `inf`. Every token whose
natural duration is ≤ 2 frames collapses to a single 25 ms frame the moment speed reaches 1.5×.
This is the structural reason a short consonant or a final phoneme can sound clipped at speed > 1:
the model is being asked to render a phoneme in less time than one frame step.

### Cost direction

**8. Native 1.5× cannot be more expensive than native 1.0×, and strategy A is strictly more expensive than B. — Inferred from code + Measured (frame counts).**
`forward_with_tokens` costs split into (a) terms that depend only on the *phoneme* sequence —
ALBERT (`model.py:102`), `bert_encoder`, `TextEncoder` (`:116`) — which are identical at every
speed, and (b) terms proportional to the *frame* count — `repeat_interleave`, the `[phonemes × frames]`
alignment matrix (`:110-112`), the two matmuls (`:114`, `:117`), `F0Ntrain` (`:115`) and the whole
decoder/iSTFTNet (`:118`). Every frame-proportional term shrinks as `1/speed`. Strategy A pays the
*1.0×* cost for (b) and then adds a time-stretch pass over the resulting samples on top. Therefore
`cost(B) ≤ cost(A)` regardless of how the fixed/variable split falls, and `cost(B@1.5) ≤ cost(B@1.0)`.
The magnitude of the saving was **not** measurable here — see 21.

**9. Cost per audio-second therefore *rises* with speed even though total cost falls. — Inferred from code.**
Because the fixed part (a) does not shrink, a 1.5× sentence costs more CPU per delivered audio second
than the same sentence at 1.0×. On the GPU this matters for the prefetch backlog (`tts_engine.prefetch`
warms 50 sentences at the current speed): the *number of sentences that can be held ready* does not
grow proportionally with speed.

**10. The real cost saving at 1.5×/2.0× is memory and disk, not wall-clock. — Measured (samples) + Inferred.**
Native 1.5× emits 33% fewer samples than 1.0× (short sentence: 105 600 → 70 800), and the
`AudioCache` row is `int16` PCM of exactly those samples (`tts_engine.py` `_write_cache_entry`, PCM at
line 250), so the cached blob shrinks by the same factor. Under strategy A the server would still
store the 1.0× blob and stretch on the fly (or store a second stretched copy — either more CPU or
more disk, never less).

### Quality

**11. Native `speed` preserves pitch; naive resampling does not. — Measured.**
`docs/research/01-speed-strategy-pitch.py`, median F0 by autocorrelation on the same sentence:

| variant | samples | duration | median F0 | vs 1.0× | semitones |
|---|---|---|---|---|---|
| native `speed=1.0` | 105 600 | 4.400 s | 196.72 Hz | 1.000 | 0.00 |
| native `speed=1.5` | 70 800 | 2.950 s | 192.00 Hz | 0.976 | **−0.42** |
| native `speed=1.0` then polyphase-resampled ×1.5 | 70 400 | 2.933 s | 292.68 Hz | 1.488 | **+6.90** |

Native 1.5× is pitch-preserving to within a fraction of a semitone (the residual is the model
re-predicting F0 for the compressed frame sequence — see 15). The naive "synthesise then resample"
reading of strategy A raises the pitch almost a fifth. **Any strategy-A implementation that is not a
true time-stretch is disqualified on this measurement alone.**

**12. Simple resampling and true time-stretching are different operations, and the difference is pitch. — Cited.**
[Wikipedia: Audio time stretching and pitch scaling](https://en.wikipedia.org/wiki/Audio_time_stretching_and_pitch_scaling)
— "When using this method, the frequencies in the recording are always scaled at the same ratio as the
speed … creating the so-called Chipmunk effect"; and separately, in its *In consumer software* section,
that pitch-corrected time-stretch is what browsers implement for **HTML media** playback.
[SoundTouch](https://www.surina.net/soundtouch/) draws the same three-way distinction in its own API:
`Tempo` (time stretch, pitch unchanged), `Pitch` (pitch changed, tempo unchanged), `Playback Rate`
("Changes both tempo and pitch together as if a vinyl disc was played at different RPM rate").

**13. WSOLA/SOLA at 1.5× is past its comfortable operating range and has a known artifact class. — Cited.**
From Olli Parviainen (author of SoundTouch), [Time and pitch scaling in audio processing](https://www.surina.net/article/time-and-pitch-scaling.html):
time-domain overlap-add methods (SOLA/WSOLA/TDHS/PSOLA) have a "tendency for producing reverberating
artefacts that get more obvious with larger time modification, i.e. when scaled time differs from the
original sound roughly by **15% or more**"; the sequence-matching is done by cross-correlating a
search window, and "if the window is set to too wide setting, the result can sound unstable, as if it
were drifting around". The same article notes SOLA's processing latency is "typically summing up to
some 100 milliseconds", and that a good phase vocoder "requires approximately an order of magnitude
more computational power than the SOLA algorithm". **A 1.5× request is a 33% modification and a 2.0×
request a 50% modification — both well past the stated 15% threshold.**

**14. The phase-vocoder alternative has a different, better-documented artifact class. — Cited.**
Same source: phase-vocoder time/pitch scaling "can result to major phase distortions causing dull
sound, which is especially notable in sharp sounds produced by instruments with a quick attack time";
and "running time or pitch scaling with Phase Vocoder for longer than a couple of second results into
practically randomized signal phases", plus "uneven frequency response, so that narrow frequency bands
over the spectrum range become highly attenuated". [Wikipedia](https://en.wikipedia.org/wiki/Audio_time_stretching_and_pitch_scaling)
adds that early implementations "introduced considerable smearing on transient ('beat') waveforms at
all non-integer compression/expansion rates … a residual smearing effect still remains", while
time-domain methods "provide the most coherent results for single-pitched sounds like voice".
**librosa's only built-in time-stretch is the phase vocoder** — verified by reading its source
(`librosa/effects.py`, `time_stretch()` → `core.phase_vocoder`), whose own docstring defers to
"`pyrubberband.pyrb.time_stretch`: High-quality time stretching using RubberBand". So "just use
`librosa.effects.time_stretch`" silently selects the artifact class in this paragraph, not the one in 13.

**15. Native speed re-predicts prosody rather than bending an existing performance. — Inferred from code.**
`F0Ntrain` runs on `en`, which is the *already compressed* alignment (`model.py:114-115`), so the F0
and noise contours are predicted for the shortened frame sequence rather than resampled from the 1.0×
contour; likewise the decoder's convolutional receptive field, measured in frames, covers proportionally
less speech-time at higher speed. Neither observation was validated by listening in this research. The
plausible failure mode is that intonation and consonant realisation drift from the model's
training-time statistics as speed rises — which is consistent with the upstream report in 16.

**16. Upstream issue #174 is real, but it is *not* the general "truncation at speed > 1" claim. — Cited (verified via API).**
[hexgrad/kokoro#174](https://github.com/hexgrad/kokoro/issues/174), title **"Can't hear the last word
when make the speed to 1.5 on Chinese"**, opened 2025-04-09 by `makao007`, body in full: *"When I try to
speed up the text , make the speed to 1.5, then I found the last word missing halt of it."* State
**closed** (2025-04-14), `state_reason: completed`. The maintainer's single comment is the most
directly relevant upstream statement for this decision: *"I think this is mitigated if you generate at
1x and then increase audio playback speed to 1.5x using other means. (Use an LLM if you want to figure
out how to do this programmatically.) It remains a TODO to root cause and/or offer this as an option."*
Two honest qualifiers: **(a)** the report is Chinese-language-specific — it was not reproduced in
English here and no English-language equivalent issue was found in the repo; **(b)** it predates the
current code, and the mechanism it would need (a zero-length final token) is now impossible because of
`clamp(min=1)` (Evidence 1). It is *directionally* consistent with Evidence 7 (71% of tokens at the
25 ms floor at 1.5×), but it is not independent confirmation of an English defect, and the maintainer's
workaround is advice, not a measurement.

**17. Upstream issue #344 is real, and it is a speed < 1 issue — it does not apply to 1.5×/2.0×. — Cited (verified via API).**
[hexgrad/kokoro#344](https://github.com/hexgrad/kokoro/issues/344), title **"speed < 1 stretches the
BOS/EOS boundary tokens too, producing an audible phantom vowel (\"a\") at clip start"**, opened
2026-07-02 by `gopalkoduri`, **open**, environment listed as *"kokoro 0.9.4, torch 2.12.1, Python 3.12
… voice `af_heart`"* — the same kokoro version installed here. Its root-cause section quotes the exact
two lines verified in Evidence 1 (`model.py:108` division and `model.py:131` BOS/EOS padding) and
measures the BOS window growing 19 → 27 frames at 0.7× with lead RMS tripling (0.0136 → 0.0396); it
proposes scaling only interior tokens (`duration[:, 1:-1] = duration[:, 1:-1] / speed`). It also states
plainly that *"`speed > 1` has the mirror-image inconsistency (shortened boundary windows); it is
inaudible in our tests"*. **The user's summary of #344 is accurate; the user's summary of #174 is
half-right** (correct on the "final-word truncation", wrong on it being a general English speed > 1
defect).

**18. Independent corroboration that Kokoro timestamps "already account for speed". — Cited.**
The [Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI) (a widely used wrapper, pinned to
kokoro 0.9.4) documents its `X-Timing-Path` sidecar as: *"`start`/`end` are seconds in the final audio,
so pauses and speed are already accounted for."* The same README notes that it clamps its own `[rate:]`
tags to 0.25–4.0 — i.e. **the clamp lives in the server layer, not in kokoro** — and that "running it
[the model] that long tends to produce 'rushed' speech and other artifacts", which is the same
qualitative direction as Evidence 15. It also documents that its Whisper WER round-trip is only run at
default speed, so it provides **no** evidence on quality at 1.5×.

**19. Community usage treats `speed` as a prosody control, not only a rate control. — Cited (weak).**
[loudterm's `kokoro-notes.md`](https://github.com/luizomf/loudterm/blob/main/docs/kokoro-notes.md)
reports that for Brazilian Portuguese "the `1.0` default works, but sometimes leaves speech hurried and
a bit flat… something between `0.88` and `0.95` usually sounds better". This is an informal project note,
not a measurement, but it indicates that the community's lever for naturalness is the *same* parameter
this decision is about — so whatever value is chosen for 1.5×/2.0× is being chosen inside the model's
own prosody control, not outside it.

**20. Browser-side "free" speed-up is not free in this app. — Verified-by-code + Cited.**
`frontend/src/lib/stores/audio.ts:217-218` sets `source.playbackRate.value = 1.0` on an
`AudioBufferSourceNode` ("backend handles speed via Kokoro native param").
[MDN](https://developer.mozilla.org/en-US/docs/Web/API/AudioBufferSourceNode/playbackRate): "When set
to another value, the `AudioBufferSourceNode` **resamples** the audio before sending it to the output"
— and its own worked example treats `playbackRate = 2.0` as an 88.2 kHz output, i.e. an octave up.
`preservesPitch` is an **HTMLMediaElement** API ([caniuse](https://caniuse.com/mdn-api_htmlmediaelement_preservespitch)),
not an `AudioBufferSourceNode` one. So the maintainer's suggested "increase audio playback speed using
other means" (Evidence 16) does **not** map onto this repo's Web-Audio path without either a true
time-stretch in JS or a switch to an `<audio>` element — and it would break the app's chunk scheduler,
which computes `nextStartTime = startAt + buffer.duration` (`audio.ts:242`).

### Repo behaviour and consistency

**21. Wall-clock and CPU-second measurements from this box are unusable — the box is oversubscribed. — Measured (negative result).**
`/proc/loadavg` read `41.42 34.18 18.43` on 16 logical CPUs, with no process of mine responsible.
Consequences in the data: the long sentence at 1.25× reported **73.75 s** wall against 12.54 s at
1.0× for *fewer* frames; and `time.process_time()` (which I tried as a contention-tolerant
substitute) reported **35.0 CPU-s for 176 frames at 1.0× but 11.6 CPU-s for 148 frames at 1.25×** — a
3× CPU drop for a 19% frame reduction, which is physically impossible and matches the known
OpenMP/threaded-BLAS busy-wait pathology under oversubscription. **Do not quote a cost ratio from
this research.** The frame, sample and F0 results are unaffected (they are deterministic).

**22. The repo already commits to strategy B in three places, deliberately. — Verified-by-code.**
`CLAUDE.md` ("Speed handled by Kokoro native `speed` param — browser `playbackRate` stays at `1.0`"),
`audio.ts:218` with its inline comment, and `tts_engine.SynthJob.speed` (`tts_engine.py:136`) being
threaded through `prefetch`, the cache key, and `join_timestamps`. There is no time-stretch code, no
`librosa`, no `soxr`, and no resampling anywhere in the tree.

**23. There are three synthesis call sites and they do not agree on speed. — Verified-by-code.**
1. `backend/services/tts_engine.py` — the real path: `_effective_speed()` (line 176),
   `_call_kokoro()` (line 185, returns `(results, effective_speed)`), cache keyed on the *effective*
   rate (`_cache_key`, line 164-173; used at lines 269, 336, 392/422).
2. `backend/routers/mp3.py:119-134` (`_synthesize`) — a **duplicated** synthesis path: its own
   try/except ladder around `_kokoro(text, voice=voice, speed=speed)`, no `_effective_speed`
   bookkeeping, **no `AudioCache` lookup or write**, and no word timestamps. An MP3 export at 1.5×
   re-renders the entire book even when every sentence is already cached from playback.
3. `backend/routers/voices.py:151` — voice previews hardcode `speed=1.0`, which is correct but is a
   third call site to keep in sync.

**24. `speed` is validated inconsistently at the WebSocket boundary. — Verified-by-code.**
`routers/tts.py:217` guards the prefetch action (`if pf_speed <= 0: continue`) but
`routers/tts.py:200` (`speed = float(msg.get("speed", 1.0))` for `play`/`seek`) does **not**.
The frontend clamps to [0.5, 3.0] (`frontend/src/lib/stores/reader.ts:54`), but the socket is the
trust boundary. Combined with Evidence 5, a client sending `speed=0` or `NaN` makes
`KModel.forward_with_tokens` raise `RuntimeError: repeats can not be negative` inside `stream_job`
(no try/except around it), which the consumer catches at `routers/tts.py:133` and converts into an
`{"type":"error"}` frame — a graceful failure, but a dead reading session with no clamp to prevent it.

**25. Choosing B is what makes the repo's per-word highlighting correct with zero extra code, and A would break it. — Verified-by-code + Inferred.**
`tts_engine._collect_result` (`tts_engine.py:208-229`) takes `t.start_ts`/`t.end_ts` straight from
misaki tokens (per Evidence 4, already in delivered-audio time) and adds `audio_offset`. The frontend
compares those offsets against `now - sentenceStart` on the `AudioContext` clock with `playbackRate === 1.0`
(`audio.ts:156-159`), so they are only correct if the delivered buffer is the buffer the timestamps
describe. Under a server-side stretch the timestamps must be divided by the stretch factor and
`audio_offset` accumulated on the stretched length; under a client-side `playbackRate` stretch the
timestamps must be divided by the rate while `audio.ts:242` (`nextStartTime = startAt + buffer.duration`)
and the whole `sentenceTimings` map must be rescaled. `_proportional_timestamps` (`tts_engine.py:143-162`) is strategy-agnostic — it distributes words across `duration_ms`, which is measured from delivered
samples — so it is the only timestamp path that survives a strategy switch untouched, and it is only
the fallback when a cache row has no stored timestamps.

**26. The user's premise about `_proportional_timestamps` is correct but it is the fallback path, not the main one. — Verified-by-code.**
It is called at `tts_engine.py:277`, inside the `cached is not None` branch and only `if word_ts is None`.
The primary path is the misaki/Kokoro `start_ts` values in `_collect_result`.

---

## Quantified tradeoffs

Cost, latency and pitch rows are substantiated above; the quality rows marked "(not measured here)"
are expectations from the cited literature, **not** listening tests performed for this report.

| Dimension | **A** — synth @1.0× + time-stretch | **B** — native `speed=1.5` (current) |
|---|---|---|
| Frame generation cost | Pays the **full 1.0× frame count** (e.g. 176 frames for the test sentence) | **118 frames at 1.5×, 99 at 2.0×** — 33–44% fewer (measured) |
| Extra DSP cost | **Added**, and it is not small: WSOLA ≈ CPU-cheap but needs cross-correlation; phase vocoder ≈ "an order of magnitude more computational power than SOLA" (Parviainen) | **None** |
| Total CPU/GPU per sentence | `cost(1.0×) + stretch` — strictly the larger of the two (inferred from code) | `cost(speed)` — strictly ≤ 1.0×, non-trivially less at 1.5× (inferred) |
| CPU per *audio second* | Rises (1.0× work ÷ shorter audio, plus stretch) | Also rises vs 1.0×, because the phoneme-side terms do not shrink (inferred) |
| GPU relevance | Same as CPU (`torch.cuda.is_available()` = False here; T600 path **not** measured) | Same |
| Time-to-first-chunk | **Worse**: a lookahead-based stretch needs ~100 ms of future samples per sentence (SOLA) or a full FFT window; a phase vocoder is stated to decorrelate past ~1–2 s, pushing toward whole-sentence batch processing — which fights `routers/tts.py:100-102`'s chunk-by-chunk streaming | Unchanged: frames stream as they are produced; fewer frames means the first chunk arrives sooner |
| Audio quality — pitch | Preserved **only** with a true TSM; naive resampling measured at **+6.9 semitones** | Preserved: measured **−0.42 semitones** at 1.5× |
| Audio quality — artifacts | WSOLA: reverberant/doubling past ~15% change (1.5× = 33%, 2.0× = 50%). Phase vocoder: transient smearing, "dull sound", uneven frequency response, phase decorrelation past a couple of seconds | Prosody/F0 are re-predicted on a compressed frame grid rather than bent from a natural performance; **71% of tokens hit the 25 ms floor at 1.5×, 91% at 3.0×** — consonant/final-phoneme clipping risk. Not listening-tested here |
| Upstream issue exposure | Avoids #174 (truncation) and #344 (phantom vowel), both of which are native-`speed` bugs | Inherits #174's mechanism risk (Chinese-specific report; `clamp(min=1)` rules out a zero-length token) and, at **speed < 1 only**, #344 (BOS window 19→27 frames, phantom initial vowel) |
| Disk cost (AudioCache PCM, int16) | Stores the 1.0× blob (largest) and either stretches per play (CPU) or stores a second blob (larger) | Stores exactly what is played: **interpolating my measured samples, a 1.5× row is ~33% smaller, a 2.0× row ~44% smaller** than the 1.0× row |
| Streaming / prefetch | Stretch must be inserted between synthesis and the 100 ms chunker, or per sentence before caching — both touch the latency-critical path | No change to the streaming path; `prefetch(..., speed, ...)` already keys correctly |
| Word timestamps | **Breaks** `_collect_result` (start_ts is 1.0×-time) and the frontend's `elapsed` comparison; needs an explicit `/stretch_factor` on every timestamp plus `audio_offset` on stretched lengths | **Correct with zero code**: measured 4.150 → 2.750 s for the last word, consistent with the frame ratio |
| Implementation complexity | New DSP (WSOLA ≈ 100 lines of careful numpy, or `librosa` → pulls numba/llvmlite, a new heavy dependency in a uv-locked env), plus timestamp rescaling, plus a change to the streaming contract | **Zero**; already implemented and covered by `backend/tests/test_speed_adversarial.py` |
| Behaviour at 0.5× | TSM at 0.5× = 100% expansion, deep into the WSOLA reverberation regime; phase vocoder needs higher overlap ratios when expanding (Parriainen) | Native 0.5× works (measured 356 frames / 8.9 s) but is exposed to #344's phantom-vowel bug at the clip start |
| Behaviour at 1.25×/1.5× | The rate is exact by construction, but artifacts are added on top of an already-clean take | **Accurate** at 1.5× (1.492–1.553× measured) and 1.25× (1.180–1.189×); the model gets closest to its comfort limit at 1.5× |
| Behaviour at 2.0× | Rate exact; TSM at 50% change is at the edge of usability for WSOLA | **Under-delivers: 1.778–1.799×**, with 71% of tokens floored |
| Behaviour at 3.0× | Rate exact — **the only way to actually reach 3.0×**, since the model's own ceiling is ≈2.15× (and ≈2.51× for this sentence at `speed=∞`) | **Under-delivers badly: 2.146–2.147×**, 91% of tokens floored; the extra 1.0× of requested rate buys almost nothing |

---

## Edge cases and failure modes

1. **`speed <= 0` or `NaN` reaches the model and raises.** Measured: `RuntimeError: repeats can not be
   negative` for both `0.0` and `NaN` (Evidence 5). `routers/tts.py:200` does not validate the `play`/
   `seek` speed while `:217` does validate prefetch (Evidence 24). Result today: the consumer catches
   it (`:133`) and emits an `error` frame — a dead session, not a crash.
2. **Negative speed is silently "maximum speed".** `speed=-1.0` produces the 70-frame floor take
   (1.75 s) instead of raising (measured). A sign error becomes a nonsense-but-valid render.
3. **`speed=inf` is accepted** and behaves identically to the floor. `float("inf")` survives
   `float(msg.get("speed"))` and `json.loads` of `1e999`.
4. **Cache-key/effective-rate mismatch (partially fixed in the working tree).** The older revision of
   `_call_kokoro` caught `TypeError` and silently fell back to 1.0× *while the cache key still used the
   requested speed*, so a 1.5× row could contain 1.0× audio. The current working tree fixes this with
   `_effective_speed()` and by keying on the effective rate (Evidence 23), and
   `backend/tests/test_speed_adversarial.py` documents the residual holes (legacy poisoned rows,
   mixed rates within one session, `repr(round(speed,2))` collisions). **Rows already on disk from the
   pre-fix code are still wrong and are still reachable** — that is a migration issue, not a strategy issue.
5. **The MP3 export bypasses the cache entirely** (`mp3.py:119-134`), so exports are both slower than
   necessary and capable of diverging from playback behaviour for the same book+speed.
6. **The two speed option lists differ**: `frontend/src/lib/components/MediaBar.svelte:24` offers
   `[0.5 … 3.0]` while `frontend/src/routes/mp3/+page.svelte:19` offers `[0.5 … 2.0]`. Combined with
   Evidence 6, the reader UI advertises a 3.0× that the model cannot deliver.
7. **Sentence-duration metadata is derived from delivered samples**, so it stays correct under B
   (`_audio_duration_ms`, `tts_engine.py:231`) but would be wrong under client-side stretching unless
   recomputed; the frontend deliberately uses only that value now
   (`AudioProgressBar.svelte:12-21`, `audio.ts:318-324`) and derives position from the sentence index,
   so there is no extrapolation from `speed` left to be misled by Evidence 6.
8. **Speed > 1 compounds with the 510-phoneme chunk limit.** `pipeline.en_tokenize` splits at 510
   phonemes (`pipeline.py:206`); the frames-per-chunk ceiling is 510 at the one-frame floor, so long
   chunks render as an unbroken ~12.75 s of maximally compressed speech with no internal pause — the
   "rushed speech" failure mode that Kokoro-FastAPI chunks around at 175–250 tokens (Evidence 18).
9. **`speed < 1` is a separate, unfixed upstream bug** (#344): at 0.7× the BOS window grows 19 → 27
   frames and is filled with voiced audio, heard by Whisper as a leading article in 13/20 words. This
   app's default is 1.0×, so it is only reachable if the user picks 0.5×/0.75×.
10. **Cloud/remote path.** `backend/services/modal_remote.py` forwards `speed` verbatim
    (`build_payload`, line 418-424) and rehydrates `start_ts`/`end_ts` (`KokoroToken`,
    `_decode_tokens`). **If the remote worker does not run the same kokoro version, the frame-floor and
    timestamp findings above do not automatically carry over** — the wire contract preserves the fields,
    not the semantics.

---

## Recommendation for this repo, with the concrete change

**Keep strategy B.** The evidence is asymmetric: B is what the code already does, it is strictly
cheaper, it preserves pitch, and it keeps word highlighting correct at zero cost; A requires a new
DSP dependency (or a hand-written WSOLA), a change to the streaming contract, and explicit timestamp
rescaling, in exchange for a quality improvement that **nobody has measured here**. The one
mainstream upstream voice on the topic (the Kokoro maintainer on #174) recommends the *opposite*, but
that advice is aimed at a Chinese-specific truncation bug in an older release and lands on a
`AudioBufferSourceNode` path in this app where "playback speed" means resampling and therefore a
+6.9-semitone pitch shift (Evidence 11, 16, 20).

Concretely:

1. **Freeze the strategy in a comment where it can be found** — `tts_engine.SynthJob.speed` — stating
   that `speed` reaches the *duration predictor before frame generation*
   (`kokoro/model.py:108`) and is therefore not a resample, so that a future "optimisation" to a
   time-stretch is not made on the false premise that Kokoro renders at 1.0× and throws the frames away.

2. **Clamp and validate `speed` in `routers/tts.py` where the socket is read** (the `play`/`seek`
   branch, ~line 200), mirroring the existing prefetch guard at line 217:

   ```python
   speed = float(msg.get("speed", 1.0))
   if not math.isfinite(speed) or not (0.5 <= speed <= 3.0):
       speed = 1.0          # or: send an {"type":"error"} frame and continue
   ```
   This closes the `0`/`NaN` `RuntimeError` path (Edge case 1) and the `-1`/`inf` pathological-accept
   paths (2, 3) at a single place instead of in three call sites.

3. **Fix the range's honesty rather than the DSP.** Because achieved rate saturates (Evidence 6), the
   highest-value change is to stop offering a rate the model cannot reach: either
   (a) reduce `MediaBar.svelte:24` to `[0.5, 0.75, 1.0, 1.25, 1.5, 2.0]` — matching the MP3 page and
   removing the two settings (2.0 → 1.78×, 3.0 → 2.15×) whose labels overstate what the user gets — or
   (b) keep 3.0 but label the top of the range as approximate. This is a one-line UI change and needs
   no new dependency. **It is also the only change here that a user can perceive.**

4. **If a true rate above ≈2.1× is ever a hard product requirement**, the correct design is a *hybrid*,
   not a wholesale switch to A: request `native_rate = min(requested, 1.5)` from Kokoro and apply the
   residual factor (`requested / 1.5`, i.e. 1.33× at 2.0× and 2.0× at 3.0×) with a time-stretcher.
   That halves the demand on both failure modes at once — the token floor (Evidence 7) and the
   time-stretch artifact budget (Evidence 13). Implement it **server-side, per sentence, inside
   `_write_cache_entry`**, so the stretched buffer is what gets cached, streamed in 100 ms chunks, and
   described by `duration_ms`; then divide the `start_ts`/`end_ts` values in `_collect_result`
   (`tts_engine.py:208-229`) by the same residual. Do **not** implement it with `playbackRate`
   (Evidence 20) and do **not** use `soundfile`/`scipy` resampling for the rate change (Evidence 11).
   `librosa` is not currently installed and its `time_stretch` is a phase vocoder; if a dependency is
   acceptable, prefer a WSOLA implementation for speech, or `pyrubberband` **only if** shipping the
   `rubberband` binary is acceptable for the deb/Flatpak targets — a self-contained numpy WSOLA avoids
   that packaging question entirely.

5. **Consolidate the duplicated synthesis path.** `mp3.py:_synthesize` (lines 119-134) should go
   through `TTSEngine` (respecting `AudioCache` and `_effective_speed`) rather than re-implementing
   the call ladder; today an export re-renders audio the user has already played, and the two paths
   can disagree about which rate was actually honoured.

6. **Optional, and only if 0.5×/0.75× matters:** adopt the #344 mitigation (scale only interior tokens)
   so that the slow settings do not prepend a phantom vowel. It is a ~2-line change in a patched copy
   of `forward_with_tokens`; it does **not** help 1.5×/2.0×, and #344 reports that speed > 1's
   mirror-image boundary inconsistency is inaudible.

---

## Open questions / what we could not verify

1. **No listening test.** Nothing in this report is a perceptual judgement. The comparison
   "native 1.5× sounds better/worse than good WSOLA at 1.5×" is **unresolved**. The strongest available
   proxy is structural (Evidence 7: 71% of tokens at the 25 ms floor at 1.5×), and it points *against*
   native at high speed — so the recommendation is "keep B for 1.25–1.5×, and fix the range" rather than
   "B is better at 2.0×". **A blind A/B at 1.5× and 2.0× on 20 real sentences from this library is the
   single experiment that would settle it**, and it needs a working audio output, not a benchmark box.
2. **No GPU measurement.** The T600 is invisible here (`torch.cuda.is_available()` is `False`,
   `/dev/nvidia*` absent). Every cost statement is either CPU-side or an inference from frame counts.
   The *direction* (`cost(B) ≤ cost(A)`) is architecture-independent; the *size* of the saving on the
   T600 is not established, and the realtime factor on a T600 will be far better than the ~1x CPU
   figures in `01-speed-strategy-bench.json`, which that file labels as sandbox-limited.
3. **Cost ratios are unmeasured** — the box was at load average ~41 (Evidence 21). `01-speed-strategy-bench.py`
   and `01-speed-strategy-durationmodel.py` re-run cleanly on an idle machine and would produce usable
   wall-clock and CPU-time curves; the scripts are committed under `docs/research/`.
4. **#174 was not reproduced in English.** It is a one-line Chinese-language report, closed without a
   root cause. I verified it exists and says what the task claimed (with the language qualifier), but I
   could not confirm or refute an English final-word truncation at 1.5× without listening.
5. **Whether the frame floor's consonant clipping is audible at 1.5×** is unverified. The mechanism is
   measured (71% of tokens at 25 ms); the perceptual consequence is inferred from the fact that 25 ms is
   one vocoder frame step.
6. **The upstream `speed` semantics could change.** Both findings 1 and 6 are pinned to `kokoro` 0.9.4
   (`model.py:108`). `#344` is open with a proposed patch that would change exactly that line, and
   Kokoro-FastAPI already clamps inside its own layer. A `uv sync` that moves the pin is a silent
   semantic change to this app's entire speed feature.
7. **The KTH thesis comparison table** (MOS-style scores for OLA/SOLA/phase vocoder/élastique at 1.5×)
   surfaced in search results at
   `https://kth.diva-portal.org/smash/get/diva2:1985503/FULLTEXT01.pdf` but the PDF could not be
   retrieved with the available tooling, so **it is deliberately not cited as evidence here.** It looks
   like the most relevant existing perceptual study for a future pass.
8. **Sentences were not sampled from the real library.** Both benchmark sentences used above are
   synthetic. Token density (tokens ÷ 1.0× duration) sets the ceiling in Evidence 6, and that quantity
   varies by sentence, so a per-book average would be needed to state what 3.0× means for real content.
   A sibling artifact in this directory, `docs/research/bench_synth.json`, now provides the real-corpus
   1.0× baseline (30 sentences stride-sampled from `cleancodebook.pdf`, 9070 speakable sentences in the
   book: mean warm wall 9.55 s, median 8.99 s, mean warm RTF 1.77, median 1.62, median audio 6.3 s — on
   the same contended CPU, so the same caveat as Open Question 3 applies). **It has no speed axis**:
   the one experiment this research leaves open is precisely `that corpus × the speed axis`
   (`bench_synth.py --speeds`), which would turn the token-density ceiling from a two-sentence
   observation into a per-book number. (Its first run failed with
   `sqlite3.OperationalError: near "index": syntax error` because `index` is a SQLite reserved word;
   quoting it as `ORDER BY s."index"` fixed it.)

## Artifacts produced by this research

All under `docs/research/` (nothing outside it was modified):

| File | What it establishes |
|---|---|
| `01-speed-strategy-bench.py` / `.json` / `.log` | Wall-clock, wall-clock caveat, sample+frame counts for two sentences × 7 speeds |
| `01-speed-strategy-durationmodel.py` / `.json` | The per-token duration vector at 1.0×, the analytic `clamp(min=1)` model vs measurement, token-floor occupancy, and the CPU-time (contaminated) series |
| `01-speed-strategy-range.py` | Extreme/illegal `speed` values: `0` and `NaN` raise, `-1`/`inf` silently clamp to the floor |
| `01-speed-strategy-pitch.py` | Median-F0 measurement: native 1.5× = 0.976× (pitch kept), resample-to-1.5× = 1.488× (+6.9 semitones) |
| `01-speed-strategy-timestamps.py` | Word timestamps shrink with speed and stay inside the audio; last-word end_ts 4.150 → 1.900 s |
