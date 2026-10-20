# NaturalReader playback pipeline — findings from public bundles

Reverse-engineered from NaturalReader's **public web bundles only** (no login, no session, no audio
captured). Design inspiration for this repo's process-first architecture; nothing here is copied code.

## Scope & method

- Bundles fetched from `https://www.naturalreaders.com/online/ngsw.json` (38 JS chunk URLs).
- Downloaded to `/tmp/nr-bundles/`, prettier-formatted copies in `/tmp/nr-bundles/pretty/`.
- All `file:line` references below are into the **pretty-printed** copies:
  - `23.69f45db6b980fef4.js` — TTS core: services, streaming socket, preload policy, orchestrator
  - `597.51955e0c3b80c08a.js` — readers/UI: PDF drive-mode, CC textview, highlighting consumers
  - `112.387cb9b99757faa5.js` — AskAI reader (same highlight consumer pattern)
  - `main.5de44f81d84ab80a.js` — Supertonic on-device engine (ONNX), asset caching
- REA static analysis (evidence `ev_41a22412…`) was used to map module structure; all findings below
  verified by direct reading of the formatted sources. Mangled identifiers are shown with approximate
  role names.

## Architecture map (chunk 23)

| Symbol (role) | Line | What it is |
|---|---|---|
| `class i` — audio record | 8068 | `{url, text, marks, err, isSpeedAdjusted=true, isStreamComplete=true, audioBuffers[], pcmSampleRate, pcmChannels, pcmEncoding, pendingFinalReplacement, voiceSource}` |
| `class l` — WebAudio player | 8085 | `AudioContext` + `GainNode`; wraps `AudioBufferSourceNode`; `playbackRate` setter → `source.playbackRate.setValueAtTime()` (8161–8174); `createBufferFromPcm(bytes, rate=24000, ch=1, "pcm16")` (8247); `scheduleBufferAtTime(buffer, when)` sets rate at schedule time (8271–8275) |
| `class u` — BufferController | 8305 | Adaptive jitter buffer (see §2.4) |
| `OnlineTtsService` | 8424–13058 | Cloud voices: streaming (WS) + REST fetch, preload cache, marks, errors |
| `FreeTtsService` | 13059 | "google" free voices; offline → native "att" fallback |
| `LocalAcaTtsService` / `CordovaTtsService` | 13382 / 13652 | Native (cordova plugin) wrappers |
| `SupertonicTtsService` | 13974+ | On-device "lite/st" voices, prefetch pool, `playToken`, `prefetchGeneration` |
| TTS orchestrator (`v`) | 14499+ | Message-bus consumer; play/pause/seek, preload scheduling, layout/text prediction |
| Preload policy (module 97408) | 12865–12966 | `computeNumPreloads`, keep-range, validity, keys |

Message names observed (publish/subscribe via a MessageService):

- `VRv` sentence started (`{pageNo, index}`), `WfZ` CC text-segment update, `mWY` word highlight
  (`{word}`), `qSK` sentence played to end, `PM` buffering ("load" state), `JED`/`W0b`/`Ezr`/
  `z13` pause/finish/stop, `MMP` speed changed, `rNO` speech-marks disabled state, `TsD` voice/settings
  changed, `zt3` secondary-voice switch, `AA4` reader state changed, `jkt`/`NCR` stop/jump, `Il6` TTS
  limit error, `qB`/`dqW` license wait/ok.

---

## 1. Prefetch & eviction algorithm

### 1.1 Policy (module 97408, 23.js 12865–12966)

- `computeNumPreloads(currentIndex, page)` (12940 → class G, constructor ~12928):

  | situation | `{prev, next}` |
  |---|---|
  | background processing (PDF parse in background) | `{2, 8}` |
  | "lite" voice | `{2, 4}` |
  | ≤ 1 sentence left on page (page end → next-page lookahead) | `{2, 8}` |
  | default | `{2, 4}` |
  | after N errors (see §4) — non-background | `{2, 2}` |

  The doc previously assumed `{2,4}`/`{1,1}`: the **service-side fallback default** is `{1,1}`
  (used when a reader has no policy value yet or is degraded); the **policy default is `{2,4}`**.
- Preload key (export `Tf`, 12953 area): `` `${page} ${index} ${voiceKey}` `` — page, sentence index,
  voice; stored in a `preloadsBySource` Map on the service (in-flight entries marked separately).
- Preload-capable readers (export `V`): `"OnlineTtsService" || "SupertonicTtsService"` only.
- Voice routing validity (export `P6`): free voices rejected offline unless they are the "att" (native
  Apple TTS) source; "st" voices only via `SupertonicTtsService` (export `M5`: `source === "st"` + id
  + models `"ready"`).

### 1.2 Per-sentence scheduling (orchestrator, 15065–15162)

`schedulePreload(page, index)` runs after every sentence-start and does:

1. `++preloadWalkGeneration`; `const stale = () => gen !== this.preloadWalkGeneration` — any later
   `schedulePreload` call kills the previous walk between awaits (walk = the whole scan below).
2. Resolves positions **sentences-first, not pages**: for `E = 1..next` and then `E = 1..prev` it uses
   `getPageNoAndIndexOfNNextSentence(E, page, index)` / `…Prev…` so the scan crosses page boundaries
   correctly. `collectPreloadPositionsInRange` (12953, also 10334/8451) builds the same
   `{page, index, voiceKey}` position list used by the service-side walkers.
3. Per position: `processText` (sentence segmentation/layout), resolve the **voice key via a
   `H(text, settings, {useSecondaryReader, prevText, direction})` resolver** that can flip between
   speaker A/B (secondary reader) mid-walk; skip invalid sentences.
4. Route to engine:
   - `"st"` voice + `modelStatus() === "ready"` → queue `supertonicTts.preloadAudio(page, index, id)`
   - else valid online voice → `onlineTts.preloadFromOrchestrator(page, index, key, batchPos)`
     (`batchPos === 1` also remembers "first-next" keys used for the native-streaming fallback logic,
     `batchPos === 2` runs `maybeForceNativeGmnForStreaming`: if the buffered audio duration of the
     current streaming sequence is shorter than `4 s / (speed/180)` and the stream is not complete,
     it **drops the streaming preload and re-requests via native/REST** so slow streams don't stall).
5. Runs the queued prefetch tasks through a shared bounded runner: `W(tasks, concurrency=2, cancelFn)`
   — **max 2 concurrent prefetch tasks** (`Od` = 2). `cancelFn` = walk-stale OR supertonic
   prefetch-generation changed.
6. Finally `removePreloads(page, index)` on both services — eviction.

Additionally, on play the orchestrator warms **non-audio state** ahead of reading:
`predictNextLayout(page+1..2)` pre-renders the next two pages' sentence layout and
`predictNextHFF(page, index)` walks the next 5 sentences and predicts text features for unlabeled
ones (`others`/`plain-text` labels) so segmentation is ready before those sentences are read
(15004–15022).

### 1.3 Eviction (`removePreloads`, ~12760; `preloadRangeToKeep` = `{prev: 10, next: 20}`)

Preload keys outside the keep range relative to the reading position are deleted after each
`schedulePreload` walk resolves. Because eviction runs on the *current* position, the effective cache
window slides forward; in-flight entries inside the window survive seek (they become immediately
useful), and entries far behind (browsing/seek backwards) get evicted.

### 1.4 Supertonic prefetch pool (13974–14190)

- State: `prefetchCache` Map, `prefetchGeneration`, `playToken`, default `numPreloads {2,4}`.
- `startPrefetchBatch()` → `++prefetchGeneration` and returns it. **Pause bumps generation (cache
  kept); stop clears the cache and bumps; each queued task re-checks `generation` and `playToken`
  before running and `synthesize()` is only invoked if still current.**
- Task result: WAV blob cached under page/index/voice; playback = `URL.createObjectURL(blob)` on an
  `<audio>` element, `revokeObjectURL` on stop.
- Usage gate: free-char quota via `ttsUsageService.canUseCharacters` else throw.
- Synthesis: `supertonic.synthesize(text, voice, "en", 8, 1)` — 8 refinement/denoise steps,
  speed 1 (baseline). Speed is applied at playback (§5).

### 1.5 What happens on seek / voice change / speed change / pause

| event | online service | supertonic service |
|---|---|---|
| **seek / play(new position)** | `clearPlay()` clears current playback + stream waiters; **in-flight stream requests are not aborted** — they finish into the preload cache and are later evicted; `schedulePreload(newPos)` bumps walk generation (old walk stops between awaits). If the target sentence is preloaded → instant play. | `playToken` bumped → queued synths abort between steps; cache lookup at new position may already exist |
| **voice change** (`TsD`) or speed change (`MMP`) | `getPreloadCapableReader().clearPreloads()` — full cache drop; voice change additionally `clearPreloadsOfUnselectedVoice(oldKey)` (`zt3`) | same; supertonic also updates live `playbackRate` on speed change |
| **pause** | playback stops; preloads kept | `stopAudio()` + `++prefetchGeneration`; **cache kept** by design |
| **stop** | preloads kept until next `schedulePreload` evicts | cache cleared, generation bumped, object URLs revoked |

### 1.6 Instant sentence switching (click → play)

Why clicking a different sentence starts reading it fast. All line refs `23.69f45db…js`.

**Pre-warmed audio stack (one-time):** a single `AudioContext` is created at service construction
(8085). On the first click gesture, `setAudio()` (9833) runs `audioPlayer.prime()` (8239): resume +
play a **50 ms silent buffer** (`suppressOnEnded`), and also do silent `play()…pause()` on the hidden
`<audio>` element — unlocking both Web Audio and HTMLAudio autoplay — then a "prime pause" 50 ms
later. After that, every switch costs no context-creation/resume latency.

**Preloads make the common case a cache hit:** the orchestrator calls
`preparePreloadForPlay` + `schedulePreload` on *every* play (`checkPlayIdAndPlay`, 15304), so the
`{prev:2, next:4}` neighbours (plus `{2,8}` at page ends/background) are already fetched/decoded
while the current sentence reads. `preloadFromOrchestrator` even records the "first-next" key
(`orchestratorFirstNextKey`) for the streaming fallback check.

**The play path (9925 `play` → 9972 `playHelper`):**

1. `clearPlay()` first (9954): clears all streaming waiters, kills the CC + monitor timers,
   `audioPlayer.pause()` unless first play, `resetCurrentAudioSequence()`, `bufferController = null`
   — a **hard stop that returns immediately** (no await on audio tail).
2. `playHelper` looks up `preloads[page/idx/voiceKey]`:
   - **Cache hit** → `Promise.race([entry.blob, 20 s timeout])`, verify `blob.text === processed`
     (stale-wrong-text guard, else drop + re-synthesize), then `checkBlob` — zero network.
   - **Cache miss** → `downloadUrl(...)`; the result is itself *stored into the same `preloads`
     map* — play and preload are literally one code path, so in-flight prefetch work for the
     clicked sentence is **reused, not duplicated** (the race awaits the same promise the prefetch
     started).
3. `checkBlob` (10017) and `setPlay` (10047) re-check `playId` **three times** (arrival, after blob
   await, after 50 ms timer). `playId` is bumped per request by the orchestrator's `setPlayId()`
   (15295) and validated in `checkPlayIdAndPlay` (15304) — a newer click makes older work inert
   (`isProcessingPlay` off, never played; in-flight streams keep filling preloads in the background).
4. `setPlay` emits `VRv` (sentence start → UI/highlight move **immediately**), then schedules
   `innerPlay` on a 50 ms timer (10130–10153).
5. `innerPlay` (10161) → `playCurrentAudioSegment()` (11990): prefers **decoded
   `AudioBuffer`s** (PCM chunks are decoded to buffers *at receive time* — no decode-on-click) →
   `startGaplessStreamPlayback` + fresh BufferController; otherwise blob URL on the hidden
   `<audio>`.
6. The old source dies via `stopCurrentSource(true)` (8296): `a.stop()` + **`onended = null`** so the
   sentence-end chain (`qSK` → next sentence) cannot fire from the killed source; the new source
   starts on the same context in its place. Switch latency ≈ one animation frame.

**Supertonic (on-device) does the same with cheaper state** (14161 `play`):
`++playToken` → `stopAudio()` → `prefetchCache` hit (pre-synthesized WAV) or **in-flight synth
promise reused** (miss stores the promise, deduping) → stale plays abort at 3 checkpoints
(`D !== playToken`) → `playBlob(q)` → `qSK`. Played entries are deleted from the cache (14271);
pause bumps `prefetchGeneration` but **keeps the cache** (14199), so pause→resume of any preloaded
sentence is instant re-use.

**Orchestrator note:** `pause()` = `stop()`; a mid-`play`-processing pause sets
`isStoppedWhenProcessingPlay` which `setPlay` converts into a clean `stop()` (10133).

### 2.1 Connection

- Endpoint: `utilService.getEndpoint("ttsStreamingEndpoint")` → AWS SigV4-signed `wss://` URL
  (`signUrl`, 11077 area). `binaryType = "arraybuffer"`.
- **Eager connect**: socket initialized (re)on first/preemptive need and re-used across requests;
  if closed unintentionally it auto re-initializes (`initializeStreamingSocket`, 8540 area).
  Intent-flag distinguishes deliberate close (`STREAMING_SOCKET_CLOSED` error emitted early).
- close/error **fan out to every active request** (`streamingActiveRequests`, `streamingPendingContexts`)
  — all waiters reject; per-request contexts cleaned up.

### 2.2 Requests

- `fetchAudioStreaming(text, origText, endpoint, flag)` (8670+): one context per request carries
  `{responseId, text, pcmSampleRate: 24000, pcmChannels: 1, pcmEncoding: "pcm16", chunkContainer: "pcm",
  chunkUrls[], chunkBuffers[], waiters, resolveOnce, finalizeWithError, pendingMarksSegments, timeout}`.
- 60 s timer per request → `ERR_STREAM_TIMEOUT`. Response multiplexing: incoming frames carry a
  response id; `getStreamingContextForMessage` routes to the matching context (or a pending context
  waiting for its id).
- Request params include the voice/speed/marks set from `appendParamsToEndpoint` (§5) — streaming is
  created under the **"backend" speed policy** (`s = getSpeed()`), while REST uses "baseline"
  (`s = 0`) on web / "backend" on native.

### 2.3 Frames & chunks

- **Binary frames** (ArrayBuffer/Blob): raw PCM payload → `handleChunkBuffer` (8986–9020) serializes
  decode through `processingPromise` (strict order): `audioPlayer.createBufferFromPcm(bytes, 24000, 1, "pcm16")`
  → `deliverChunk(url: "pcm-chunk-N", audioBuffer)`; fallback (no Web Audio) = Blob
  `audio/L16;rate=24000;channels=1` + objectURL.
- **Text frames** (JSON): header fields (`sampleRate`, `pcmEncoding`…), **base64 audio chunks**
  (`base64ToArrayBuffer`), `speech_marks` / `marks` segments held as `pendingMarksSegments`, and the
  **final update**: `handleFinalUpdate` (8810) may be an HTTP URL to a *complete* WAV — stored as
  `pendingFinalReplacement`; when the streamed sequence ends, the streamed chunks are replaced by the
  final file for **gapless continuity** (`applyPendingFinalStreamUpdate`).

### 2.4 Playback of chunk N+1 vs N

- If per-chunk `audioBuffers` exist and `!isStreamComplete` → **gapless Web Audio path**
  (`startGaplessStreamPlayback` + BufferController): first buffer starts a source node; subsequent
  buffers are scheduled with `scheduleBufferAtTime(prevEnd)`; a monitor interval (12021) evaluates
  `shouldStartPlayback`/buffer health.
- BufferController (8305): adaptive jitter buffer —
  `minBufferMs 300, maxBufferMs 2000, EWMA α=0.15` on arrival gaps, `jitterMultiplier 2`, underrun
  penalty `max 800 / step 120 / decay 20`; `target = clamp(300 + 2·jitter + penalty, 300–2000)` ms.
  Playback is gated on ≥ target buffered; underruns raise the target (slow-machine resilience).
- No buffers (e.g. REST blob) → hidden `<audio id="nr-pw-non-streaming-audio">` with a blob URL.
- `setOnEnd` per segment → `tryPlayNextStreamSegment()` → otherwise apply final replacement →
  emit `qSK` (sentence ended) → orchestrator `playNext()`, which triggers the next sentence's play +
  the next preload walk.

### 2.5 Cancellation & reconnect

- Per-request: `resolveOnce` guards double-settle; 60 s timeout; `finalizeWithError` codes:
  `ERR_STREAM_INIT/PROCESS/DECODE/UNSUPPORTED/FAILED/CLOSED/CONNECTION`, `STREAMING_SOCKET_CLOSED`.
- On close/error: all active requests reject; contexts removed; the current download attempt falls
  back to the **REST fetch path** for the same sentence (see §4), and the socket is re-initialized
  lazily on next use (or when marks are re-enabled, 12820–12826).
- Seek does not kill the socket; stale streams finish into preloads (bounded by eviction).

---

## 3. Highlighting & word timing

This was the open question. Answer: **server marks, with wall-clock interpolation and char-rate
estimates as fallbacks — never the audio clock.**

### 3.1 Where marks come from

1. **Streaming `speech_marks` segments** (base64 in WS frames) — per streamed chunk.
2. **REST response header `x-smark`** (9430) — base64 speech-marks string for the whole audio.
3. **Dedicated speech-marks REST API** (12355+, `speechMarkEndpoint/speech-marks`, AWS SigV4):
   when a response has no marks, `attachSpeechMarksAsync` POSTs `{audio (base64), text, voiceType,
   voiceName, style, wpm, locale, instructions}`; backend returns **Whisper transcription segments**
   which `convertWhisperResponseToSpeechMarks` converts to word marks — **gated on ≥ 0.5 text
   similarity** with the original text (below → marks discarded entirely).

### 3.2 Mark format & parsing (12645–12723)

Base64-decoded payload is either a Whisper JSON (`"text" in v`) or Polly-style speech marks: first 2
chars = separator, then `word<sep>start<sep>end<sep>time` 4-tuples; overlapping/out-of-order records
are merged by KMP suffix-overlap of adjacent words.

### 3.3 Driving the highlight — `handleCC` (11646)

- A **50 ms `setInterval` wall-clock timer** walks the mark list against elapsed playback time
  (not `audio.currentTime` — confirmed: `timeupdate` appears only in a record-preview path, 597:8737).
- Time normalization (11750–11783): chunk marks are divided by the speed factor (`z = getSpeed()/180`)
  unless they come from the final/speech-mark-API path (`z = 1`, already adjusted); **"pro" voices use
  raw mark time** (server renders at speed). Timer fires `mWY {word, speechMarks}` per word and adds
  a `WfZ` chunk update when `mark.start ≥ getIndexToSwitchText(chunk+1)`.
- Text segmentation: each sentence emits one `WfZ` (CC text) message; each mark emits `mWY`
  (highlight word).
- Text segmentation detail: `ttsCcService` splits each sentence into CC display chunks
  (`getTextChunkHtml(j)`, `getIndexToSwitchText(j+1)`, `getNumTextChunks`); the CC panel shows a
  rolling chunk and switches at char boundaries.
- **No marks → estimates**: CJK = per-char timing; else split `/\s+/`;
  `estimateDuration(text, wpm) = syllables/(0.95 · wpm) · 60000` (11824) with a syllable-estimate
  heuristic (11821–11848); `oneCharTime = duration/len · 0.95` (11696); streaming without final
  update gets an estimated total, **upgraded in-flight to the real duration once chunk buffers exist
  (`upgradedToActual`, 11709–11725)**; a **one-shot correction** fires at sentence end to resync.
- Marks are **disabled** entirely after 10 min input inactivity or tab hide (6e5 ms timer,
  12763–12830; message `rNO {state:"yes"|"no"}`); re-enabling re-inits the socket if closed. This
  saves the marks fetch cost while the user is away — highlight then works from estimates.

### 3.4 Consumers

- **CC textview** (597:75100+): `VRv` → `wordIndex = -1`, show CC, `highlightSentence()`, optional
  autoscroll; `WfZ` → set text; `mWY` → sanitize word (`XRegExp` `removeNonWord`, ≤ 50 chars) →
  `wordIndex++` → `updateHighlight(word)`.
- **PDF drive-mode** (597:79430+): `mWY` → `setWordHighlight(word, sentenceIndex, wordIndex)` where
  wordIndex indexes the **k-th word token of the PDF text layer**
  (`pdfControllerService.getPdfLayerWordText(sentence, k, pageNo)`), then `wordIndex++`. If a token
  is missing, it re-walks from `lastTextviewWordHighlightIndex`, re-highlighting all previous tokens
  (79830+).
- **AskAI text reader** (112:4108+): same pattern against DOM text.

**Audio lags highlight?** The 50 ms timer can outrun audio; there is explicitly no readback from
`audio.currentTime`. The design accepts ±50 ms drift per sentence and resyncs at sentence boundaries;
if audio never arrives (stream stall) the sentence advance gate (`qSK` after segment end) holds
`isReading` off until playback catches up.

---

## 4. Slow-machine behaviour

- `errCount` per service; `handleError` (10693): ≥ 3 consecutive errors → snackbar ("Reset Reader"
  advice, 15 s) + `hasError` + **`setNumPreloads({2, 0})`** (no lookahead while degraded) — matches
  the prompt's known fact, plus a further policy drop to `{2, 2}` in `computeNumPreloads` on error.
- Specific codes skip retry entirely: `ERR_NO_RETRY`, `ERR_VOICE_NOT_FOUND`, `ERR_VOICE_EXPIRED`
  (license), invalid-license path waits for the licensing service (`qB`/`dqW`: one retry, then a
  3 s snackbar).
- **Streaming → REST fallback**: per attempt, stream failure (`ERR_STREAM_*`,
  `STREAMING_SOCKET_CLOSED`) falls through to the REST fetch inside the same try loop (10784–10830);
  `maybeForceNativeGmnForStreaming` (§1.2) also pre-emptively downgrades to REST when the buffered
  stream looks too slow (< 4 adjusted seconds ahead).
- **Buffering UI**: `PM` ("load") state drives the drive-mode spinner; the BufferController's rising
  jitter target is the mechanism that keeps playback alive on slow connections.
- Non-retryable voice-type misuse: "free"/"google"/"apple" sources on paid flows →
  `ERR_INVALID_READER_TYPE`.

---

## 5. Speed handling & pitch

- Speed unit: settings speed in WPM-ish scale where **180 = 1×**; `getFrontendPlaybackRate()`
  (10634) = `getSpeed()/180` on web, **1 on iOS in-app browser & Android** (native handles speed
  server-side).
- Request policy (10418–10513): `appendParamsToEndpoint(voice, text, source, speed, marks, policy)`:
  - **"backend"** → `s = <speed>` (server renders at speed; used by streaming (10805) and all native).
  - **"baseline"** → `s = 0` (REST on web, 10814) — server renders at baseline; client applies rate.
  - `sm = <marks requested>` — false when previewing or in background processing.
- Playback (12738–12743): `blob.isSpeedAdjusted === false` → `playbackRate = getFrontendPlaybackRate()`
  else `1`. Streaming/native blobs default `isSpeedAdjusted = true` (class `i`, 8068) and REST web
  responses explicitly set `false` (9380, 9503).
- **Pitch**: the client never uses a pitch-shift node (only `GainNode`). Rate≠1 happens on
  `<audio>` elements (REST web, Supertonic objectURL playback) where browsers default
  `preservesPitch=true`; the Web Audio source path only ever runs at rate 1 because those blobs are
  already server-speed-adjusted. Net effect: pitch is preserved everywhere via different mechanisms.
- Speed change (`MMP`): current blob keeps playing (Supertonic updates `playbackRate` live;
  non-streaming element likewise), future preloads are **cleared and re-fetched** under the new
  policy (§1.5).

---

## 6. Mapping to our process-first design

One important contrast to §1.6: NaturalReader must **pause + swap audio sources** per sentence
(separate files/buffers per sentence) and relies on preloads to hide that switch cost. We play one
immutable chapter file per speed tier, so "switching to another sentence" is just
`audio.currentTime = sentenceOffset` on the same element — no stop/start at all. Their
pre-warmed-context and stale-gating ideas are still worth copying (§7 below).

Our reader plays a fixed, immutable render (base FLAC → atempo tiers → Opus + manifest with word
timings + PDF boxes). NaturalReader's pipeline is the dynamic counterpart of the same ideas. Both
converge on: bounded lookahead, position-keyed caches, generation tokens, sentence-level advance,
and a single advancing highlight driven by wall-clock interpolation — differences are mostly
"precomputed vs on-the-fly".

| NaturalReader mechanism | Our equivalent | Change needed |
|---|---|---|
| `numPreloads {2,4}` + policy (lite/page-end/background) | whole book prerendered; tiers prebuilt | **None conceptually** — our lookahead is a *render* schedule: order chapters/sentence-chunks so early chapters are playable within seconds of upload |
| `removePreloads` keep-range `{10,20}` + key `page/idx/voice` | immutable generations + `render_store` cleanup | Add a **tier eviction policy** keyed by (chapter, voice, speed) for multi-voice/low-disk; keep-range intuition for "resume-where-read" retention |
| `preloadWalkGeneration` cancels walks between awaits | render triggers + supersede in `render_jobs` | Already present (supersede/resume). Consider a walk-style cancellation flag for the per-chapter render loop so a re-render of chapter N stops mid-scan |
| Supertonic `prefetchGeneration` + `playToken`; bounded runner `W(tasks, 2, cancel)` | render worker lease + stall watchdog | Add **generation/token check before each sentence-synthesis unit** so speed/voice/seek supersedes abort cheaply instead of restarting a whole chapter |
| Streaming WS + chunk PCM + final-WAV replacement | fixed Opus tier files, Range requests | **None needed** — our manifest + Range-serving is strictly simpler and gapless by construction; do NOT add live synthesis to the reader |
| Adaptive BufferController jitter buffer | browser `<audio>` buffering of complete files | Not needed for complete files; useful pattern **only if** we ever stream segment files at chapter boundaries |
| Highlight: 50 ms wall-clock timer + server marks + syllable fallback | manifest word timings + `audio.currentTime` rAF sync | **Keep ours** (audio-clock driven is drift-free); adopt their fallback idea: if manifest timings are missing for a sentence, estimate then one-shot correct at sentence end |
| `x-smark`/Whisper marks with 0.5 similarity gate | render-time word timings from Kokoro (no alignment needed) | For PDF "boxes": keep PyMuPDF→PDF.js box mapping; consider a similarity-style sanity gate on timings (e.g. drop manifests whose text/word count mismatch) |
| Error ladder: 3 strikes → `{2,0}` → snackbar | render status + processing screen | Add a **degraded-lookahead mode** in `render-gate.ts`/UI: on repeated tier/chapter failures, stop prefetching the next chapter, tell the user |
| Streaming→REST per-attempt downgrade | render worker engine fallback (cpu/gpu/modal) | Map the same pattern to *worker-level* fallback: if Modal render fails twice, requeue on GPU/CPU without user action |
| Marks disabled after 10 min inactivity | n/a (we always have timings) | Adopt the **battery/CPU-saving instinct** for background pages: pause rAF loop when tab hidden (already browser-cheap) |
| Speed via server render (`s`) vs client rate; pitch preserved throughout | prebuilt atempo tiers (pitch-free quality beats rate) | **None** — our tiers are the better engine of the same trade-off |

### Ranked small wins (low risk first)

0. **Pre-warm + generation-gate the player**: on first user gesture, run one silent play to unlock
   the audio path; guard every pending playback result with a per-request generation so a seek
   (or chapter jump) can never play an older resolve — NR's `prime()`/`playId` pattern (§1.6).
1. **Generation check inside the render sentence loop** (`render_worker.py`): cheap supersede without
   restarting the chapter render — mirrors `prefetchGeneration` exactly.
2. **Degraded-lookahead mode**: after N consecutive render failures for a book, reduce chapter
   prefetch depth and surface one clear status (NaturalReader's `{2,0}` ladder).
3. **Keep-range cleanup for artifacts**: extend `render_store` GC to drop old speed tiers of stale
   generations (bounded disk), mirroring `removePreloads`.
4. **Estimate-then-correct fallback** in the frontend player for sentences whose manifest timings are
   missing (rare) — syllable-based estimate + one-shot correction at sentence end.
5. **Worker-level fallback** on repeated render failures (Modal → GPU → CPU), mirroring the
   streaming→REST per-attempt downgrade.

### Things that would break our design (do not adopt)

- On-demand/live synthesis in the reader path (streaming WS, Supertonic synthesize-on-play): our
  contract is "read only from a published immutable render".
- Wall-clock timing for highlight: we already have true sync from `audio.currentTime` + manifest.
- Per-request signed ephemeral endpoints in the hot path: unnecessary once everything is prerendered.

## Unknowns (honest gaps)

- Whether the streaming server honours `s` for **all** voice families or only some (the `z`
  normalization in `handleCC` suggests chunk marks are baseline-anchored in some paths) — would need
  a HAR (the task doc's optional §6) to pin down per-family behaviour.
- Exact server-side implementation of the Whisper speech-marks endpoint (model, language handling) is
  invisible from the client bundle.
- `gmn`/`vtx`/`openai` voice-source mapping to specific vendors is inferred from param names only.
- Supertonic model internals (flow-matching synthesis, voices shipped) are not in the bundles — the
  ONNX files are downloaded at runtime; only the 4 model *names* are visible (main.js 30648–30653: `duration_predictor.onnx`,
`text_encoder.onnx`, `vector_estimator.onnx`, `vocoder.onnx`).