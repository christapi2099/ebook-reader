# Prompt: study NaturalReader's playback pipeline (for OpenCode)

Findings so far come from NaturalReader's public web bundles only (no login, no session, no audio captured).
Bundle files: https://www.naturalreaders.com/online/ (`main.*.js`, lazy chunks `23.*.js`, `597.*.js`, `272.*.js`,
`112.*.js`, listed in `ngsw.json`). Treat this as design inspiration: do not copy code, do not call their API,
do not scrape voices or audio.

## What the bundles already show

- **Sentence-level playback with a sliding prefetch window.** The on-device ("Supertonic") service keeps
  `numPreloads = {prev: 2, next: 4}`; other voice types use `{1, 1}`. After repeated errors it drops to `{2, 0}`
  (no lookahead). Preloads are keyed by page, index and voice, and anything outside `preloadRangeToKeep` is
  evicted.
- **Stale-work cancellation.** `prefetchGeneration` (bumped by `startPrefetchBatch`) and a `playToken` let a seek
  or voice change invalidate in-flight prefetches instead of racing them.
- **Cloud voices stream over a WebSocket.** Signed `wss` URL from `ttsStreamingEndpoint`, `binaryType = "arraybuffer"`,
  one socket multiplexed by response id, with per-request contexts and "chunk waiters". Socket open/close/error
  fan out to every active request.
- **Audio is played through the Web Audio API.** A player wraps `AudioContext`, `decodeAudioData`, an
  `AudioBufferSourceNode` (`playbackRate` is set on the source) and a gain node. Non-streaming paths use blob URLs
  on an `<audio>` element.
- **On-device synthesis**: onnxruntime-web (WebGPU/WebNN/WASM), model files cached in Cache Storage
  (`supertonic-tts-assets-v1`, persistent storage requested), one WAV blob per text chunk, 8 denoising steps.
- Highlighting/word timing: **not yet located**. Search `wordIndex` and `timeupdate` in `597.*.js` and `112.*.js`.

## Task for the agent

Read the chunks above (download them to a scratch directory; they are minified, so run a formatter such as
`prettier` on a copy). Answer with file and symbol references:

1. The exact prefetch/eviction algorithm: how `collectPreloadPositionsInRange`, `preloadRangeToKeep`,
   `numPreloads` and `prefetchGeneration` interact. What happens on seek, voice change, speed change, pause.
2. How the streaming socket protocol works: message shapes, how a request is split into chunks, how
   `streamingChunkWaiters` resolve, when playback of chunk N+1 starts relative to chunk N finishing, and how
   cancellation and reconnect work.
3. How highlighting and word timing are driven: from the audio clock, from server marks, or from estimates. What
   happens when audio lags the highlight.
4. Slow-machine behaviour: error counters, backoff, the `{2, 0}` fallback, any "buffering" UI state, and what
   triggers the switch from streaming to non-streaming.
5. How speed changes are handled (source `playbackRate` vs re-synthesis) and whether pitch is preserved.
6. Which of these map onto our manifest-driven, prerendered design, and which would break it.

Then propose, for this repo (see `CLAUDE.md`, process-first architecture): a progressive/chapter-first render
design with a bounded lookahead window, cancellation tokens, and per-chapter segments with chapter-local
timings. Output a table "NaturalReader mechanism / our equivalent / change needed" and a ranked list of
small, low-risk wins first.

## Needed from the user's own session (optional, improves 2-4)

A HAR captured in DevTools (Network, Preserve log, play ~1 minute with a cloud voice, skip ahead once, save as
HAR with content) with cookies and auth headers removed. It shows message order, chunk sizes and timing that the
static code does not.
