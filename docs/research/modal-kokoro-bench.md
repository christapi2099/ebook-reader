# Modal Kokoro-82M benchmark (2026-10-19)

Script: `backend/scripts/bench_modal.py`. Text: chapter 1 of a 314-page algorithms book (374 sentences,
46,638 chars, 3,231 s of audio), voice `af_heart`, speed 1.0, deployed app `kokoro-tts` (function
`synthesize`; the deployed app predates `synthesize_batch`). Client in the US to Modal; network included.

| Test | Result |
|---|---|
| First call, cold container | 18.8 s (earlier run, 2.1 s of audio) |
| First call, warm | 1.35 s / second call 0.5 s |
| Single sentence, warm (n=20) | median 0.30 s, p95 1.5 s, RTF 0.055 |
| 10 sentences in one request | 3.3 s for 96.5 s audio, RTF 0.034 |
| Whole chapter, 1 request | 75.9 s, 42.5x real time |
| Whole chapter, 4 requests (`map`) | 68.1 s, 47.4x |
| Whole chapter, 8 requests (`map`) | 59.4 s, 54.4x (first slice done at 28.5 s) |

Reading: batching sentences cuts per-call overhead (RTF 0.055 -> 0.034 -> 0.024). More parallel requests
barely helped on the interactive function: one container serialises the forward pass behind a lock and scale-out
is limited there. Real parallel prerender needs the export function (`synthesize_batch`, more containers),
which is not deployed.
