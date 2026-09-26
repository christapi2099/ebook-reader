# 02 · Synthesis strategy: full-book "precompile" vs dynamic on-demand

**Research question, scope, and evidence labels**

Should the Kokoro Reader **pre-synthesize an entire book up front** (and **re-synthesize the whole book every
time the user changes speed or voice**) instead of the current model — synthesize one sentence at a time,
on demand, while the user reads, with a small prefetch?

**Evidence labels used throughout:**

| Label | Meaning |
|---|---|
| **VERIFIED** | Read directly out of this repo's source or its SQLite DB during this research |
| **MEASURED** | I ran it on this host (`/home/christia50/Repos/ebook-reader`, i7-11850H, CPU-only) |
| **CITED** | External source, URL given |
| **INFERRED** | Derived by reasoning from the above, with the reasoning shown |
| **ESTIMATED** | A number produced by scaling a cited measurement with a stated model |

**GPU caveat (applies to every GPU number in this document).** The host has an NVIDIA T600 Laptop GPU
(4 GB, Turing, cc 7.5, driver 580.178.04 — **VERIFIED** via `/proc/driver/nvidia/gpus/`). Inside this
sandbox `/dev/nvidia*` is not exposed, so `torch.cuda.is_available()` is `False` and `nvidia-smi` cannot
reach the driver. **Every GPU figure below is ESTIMATED from cited third-party measurements. No GPU
measurement was taken on this machine.** All *measured* numbers are CPU-only.

---

## TL;DR / Recommendation

**Full-book pre-synthesis is not viable, and re-synthesizing on every speed/voice change is decisively
not viable.** The blocker is **disk, not time**: at the measured 0.97–1.87 GiB per book per (voice, speed)
at 1x, covering the 28 built-in voices × the 7 speeds the UI actually offers costs **185–355 GiB for one
book** against **529 GiB free on the entire filesystem**, and **1.30–2.48 TiB for the 7-book library** —
2.5× to 4.7× more disk than exists. On CPU, which is the path most users are on, one pass is
**15.6 hours measured**, so "re-synthesize on speed change" means 172 hours of CPU per book per voice;
the T600 estimate spans a wide **~1× to ~20× realtime**, and the only directly measured 4 GB-VRAM
datapoint found (a different, larger engine) sits at the pessimistic end. Pre-synthesis also makes
time-to-first-audio strictly worse on every book open, and there is no honest progress bar to show for a
multi-hour job. **What actually fixes the reported lag is smaller and cheaper**: the backend's synthesis
call is synchronous and blocks the FastAPI event loop for the *entire* duration of each sentence
(**83 consecutive seconds measured with zero event-loop turns**), and prefetch and playback are
**perfectly serialized** (`overlap_ratio` = **1.000**) — so prefetch steals playback latency one-for-one.
Move synthesis to a single worker thread, cap prefetch by *audio seconds* rather than 50 sentences, and
add the cache eviction that does not exist at all today. **A shipped local Kokoro reader, Recite, already
implements exactly that hybrid** ("read now, generate ahead" with a bounded look-ahead window and
`pending → synthesizing → ready` progress) — which is the strongest available evidence that the hybrid,
not full pre-compilation, is the right target.

---

## 1 · Measured facts

### 1.1 The audio cache (read-only DB queries)

`sqlite3` CLI is not installed on this host (`sqlite3: command not found`), so all queries were run
through Python's `sqlite3` module against a **read-only URI**, which cannot write even if the SQL tried
to. Nothing in this document wrote to `backend/ebook_reader.db`.

```bash
cd backend && venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect("file:ebook_reader.db?mode=ro", uri=True)
print(list(con.execute("SELECT COUNT(*), SUM(LENGTH(audio_data)), AVG(LENGTH(audio_data)), "
                       "MIN(LENGTH(audio_data)), MAX(LENGTH(audio_data)) FROM audiocache")))
print(list(con.execute("SELECT AVG(duration_ms), SUM(duration_ms) FROM audiocache")))
print(list(con.execute("SELECT COUNT(DISTINCT voice) FROM audiocache")))
print(list(con.execute("SELECT COUNT(*) FROM book")))
print(list(con.execute("SELECT COUNT(*) FROM sentence")))
EOF
```

Exact SQL and raw results:

| SQL | Result |
|---|---|
| `SELECT COUNT(*) FROM audiocache` | **4553** |
| `SELECT SUM(LENGTH(audio_data)) FROM audiocache` | **808,521,600 bytes** (771.1 MiB) |
| `SELECT AVG(LENGTH(audio_data)) FROM audiocache` | **177,579.97 bytes** |
| `SELECT MIN(LENGTH(audio_data)), MAX(LENGTH(audio_data)) FROM audiocache` | 10,800 / 1,243,200 bytes |
| `SELECT AVG(duration_ms), SUM(duration_ms) FROM audiocache` | **3,699.58 ms** / 16,844,197 ms (4.679 h) |
| `SELECT COUNT(DISTINCT voice) FROM audiocache` | **3** (`am_adam` 2686, `af_heart` 1834, `am_michael` 33) |
| `SELECT COUNT(*) FROM book` | **7** (2 real PDFs + 5 ephemeral text snippets) |
| `SELECT COUNT(*) FROM sentence` | **12,172** (11,301 speakable) |
| `SELECT SUM(LENGTH(word_timestamps)) FROM audiocache WHERE word_timestamps IS NOT NULL` | 1,767,168 bytes (1,777 rows have none) |
| `PRAGMA page_count × page_size` | 205,055 × 4096 = **839,905,280 bytes** = exact file size; `freelist_count` = **0** |
| `SELECT MIN(created_at), MAX(created_at) FROM audiocache` | 2026-04-20 → 2026-05-13 |
| `SELECT substr(created_at,1,10), COUNT(*) ... GROUP BY 1` | 04-20: 1316 rows; 04-27: 112; 05-03: 341; **05-13: 2784 rows / 456 MB in one day** |

**Derived format fact (VERIFIED against the writer and the data):** `808,521,600 ÷ 16,844.197 s` =
**48,000.0 bytes per second of audio** — exactly 24,000 Hz × 2 bytes, i.e. headerless mono `int16` PCM.
This matches `tts_engine.py:109` (`(full_audio * INT16_MAX).clip(...).astype(np.int16).tobytes()`).
**Every disk figure in this report follows from this one constant.**

**No speed column.** `CREATE TABLE audiocache (text_hash VARCHAR NOT NULL, audio_data BLOB NOT NULL,
duration_ms INTEGER NOT NULL, voice VARCHAR NOT NULL, created_at DATETIME NOT NULL, word_timestamps
TEXT, PRIMARY KEY (text_hash))` — **VERIFIED**. Speed is baked into the SHA-256 key
(`tts_engine.py:61`, `f"{text}:{voice}:{speed}"`) and is **not recoverable from the table**. Consequence:
the measured 3.70 s average sentence duration is a *blend across whatever speeds were used*, and is
therefore a **lower bound** on the 1x duration. This is why §2 carries two estimates.

> **Integrity note (disclosed for honesty).** The DB file was **839,905,280 bytes** when measured and
> **839,917,568 bytes** (+12,288 = exactly 3 SQLite pages) when re-checked at the end of this research —
> i.e. the file grew while the research was in progress. **No data changed:** re-running the queries
> returned byte-identical results (`COUNT(*) = 4553`, `SUM(LENGTH(audio_data)) = 808,521,600`,
> `AVG = 177,579.96925104328`, `SUM(duration_ms) = 16,844,197`, `freelist_count = 0`, and `MAX(created_at)`
> is still 2026-05-13 with **zero** rows newer than that). The growth is consistent with a **concurrent
> process in the same workspace** (an unrelated agent's test run exercising
> `db/database.py:create_engine_and_tables()`), not with this research. **All three of my scripts open the
> database exclusively as `sqlite3.connect("file:...?mode=ro", uri=True)`** — verified by grep over
> `bench_synth.py:23`, `bench_blocking.py:27` and `calc_arithmetic.py:17` — which cannot write. Every
> number in this report was confirmed unchanged after the growth.

### 1.2 How many sentences does a real book have?

Sentences come from spaCy sentence segmentation, not regex: `base_engine.py:41-50`
(`self.nlp(text)` → `doc.sents`, dropping sentences with `< min_words` non-punctuation tokens).
PDF adds word-level geometry (`pdf_engine.py:24-112`), EPUB/text get zero coordinates.

The DB contains one genuinely large real book, already ingested:

```sql
SELECT b.title, b.page_count, COUNT(s.id),
       SUM(CASE WHEN s.filtered=0 THEN 1 ELSE 0 END)
FROM book b LEFT JOIN sentence s ON s.book_id=b.id
GROUP BY b.id ORDER BY 2 DESC;
```

| Book | Pages | Sentences | Speakable (`filtered=0`) | Sentences/page |
|---|---|---|---|---|
| **cleancodebook.pdf** | **462** | **9,663** | **9,070** | **20.92** |
| gnu-c-manual.pdf | 91 | 2,483 | 2,205 | 27.29 (dense C manual, many code fragments) |
| 5× "Untitled Text" (ephemeral) | 1–17 | 26 | 26 | — |

Average speakable sentence length: **85.5 characters** (`SELECT AVG(LENGTH(text)) FROM sentence WHERE
filtered=0`), 93.86% of extracted sentences are speakable.

**Estimate for a typical 300-page book: ≈ 6,275 sentences, ≈ 5,890 of them speakable.**

Two independent cross-checks:
1. Scale by page density: 300 pages × 20.92 sentences/page = **6,275**.
2. Scale by word count: 5,890 sentences × 85.5 chars ÷ 5.9 chars-per-word ≈ **85,400 words**; a
   conventional 300-page book is ~90,000 words. Agreement within 5%.

**Use N ≈ 5,900 speakable sentences per 300-page book** for all arithmetic below.

### 1.3 Time to synthesize one sentence — MEASURED (CPU)

`docs/research/bench_synth.py` loads `KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M",
device=device)` exactly as `backend/main.py:_init_kokoro` does, then synthesizes a deterministic
30-sentence stride sample drawn read-only from `cleancodebook.pdf` (so the sample spans the whole book,
not just front matter), at `voice="af_heart"`, `speed=1.0`.

```
cd backend && venv/bin/python ../docs/research/bench_synth.py
```

| Metric | Value |
|---|---|
| Host device | **CPU** (`torch.cuda.is_available()` → `False`; `/dev/nvidia*` absent in sandbox) |
| torch | 2.14.0+cu130, `torch.get_num_threads()` = **8** (of 16 logical / 8 physical cores) |
| CPU | Intel **i7-11850H** @ 2.50 GHz, 8C/16T |
| Model load (`KPipeline` construction) | **6.42 s** cold, **16.80 s** warm-run |
| Sampled sentences | 30 (29 warm) |
| **Mean wall time / sentence** | **9.55 s** |
| **Median wall time / sentence** | **8.99 s** |
| Min / max / p90 | 1.94 s / **24.30 s** / 16.60 s |
| Mean audio produced / sentence | 6.30 s |
| **Mean real-time factor (RTF)** | **1.77×** (median 1.62×, max 3.79×) |
| Throughput | **0.105 sentences/second** |
| Bench wall total | 305.09 s for 30 sentences |

**Interpretation: on this CPU the synthesizer runs ~1.8× slower than real time.** Producing one second of
speech costs ~1.8 seconds of CPU.

**Independent triangulation — my CPU number is not an artifact of this laptop.** Three separate
measurements of Kokoro-82M on three different CPU classes all land in a narrow band:

| Source | Hardware | Runtime | Measured RTF |
|---|---|---|---|
| **This report (MEASURED)** | Intel i7-11850H, 8 threads | PyTorch | **1.77** |
| [Google LiteRT](https://huggingface.co/litert-community/Kokoro-82M/commit/325fa6eb4c9cd7b96b5481976c5bd91ffeea40d5) | Pixel 8a (Tensor G3), 4 threads, fp32 | LiteRT | **≈1.8** |
| [sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/rtf.html) | Raspberry Pi 4 Model B, 4 threads | ONNX | **2.77** |
| [gauravvij benchmark](https://github.com/gauravvij/kokoro-tts-vs-supertonic-3-tts) | 4-core Xeon | PyTorch / ONNX | 1.3 / 1.8 |

**This is an important negative result:** Kokoro's CPU cost is **hardware-insensitive across a phone, a
Pi 4, a Xeon and an i7** — everything sits at roughly 1.3–2.8× real time. The model is the floor, not
the machine. **Consequence for the strategy decision:** buying a faster CPU will not make on-demand
synthesis keep up, and no amount of prefetch tuning fixes a machine that is 1.8× too slow. Only the GPU
path (or accepting the stall) changes this. For contrast, on the *same* Pi 4 the sherpa-onnx table shows
**Piper at RTF 0.349 — ~3× faster than real time** on a 61 MB model, which is the quality/speed trade
this class of app has to make.

> **Correction to the brief.** The task brief stated the repo's docs/issues mention *6–24 s/sentence on
> CPU*. I could not find that claim anywhere in the repository
> (`grep -rni "6-24\|per sentence\|sentences per minute"` over `*.md/*.py/*.ts/*.svelte`, excluding
> `venv`/`node_modules`/`.uv-cache` → nothing). It is **unverified in-repo**. My own measurement
> happened to land on **1.94–24.30 s**, bracketing that range, so the figure is probably real but came
> from an issue thread or a prior conversation, not from this checkout.

### 1.4 Where the time goes, and what that implies for a fix — MEASURED

`docs/research/bench_blocking.py` splits the cost the way `tts_engine.py` actually incurs it, and
measures how long the asyncio event loop is starved while it happens.

```
cd backend && venv/bin/python ../docs/research/bench_blocking.py
```

| Measurement | Value | What it means |
|---|---|---|
| **misaki G2P mean** (`g2p(text)` → tokens) | **0.013 s** (0.0046 / 0.0073 / 0.027) | Pure-Python, GIL-bound, and **negligible** |
| **torch model forward mean** (`pipe.model(ps, pack, 1.0, return_output=True)`) | **30.90 s** (25.19 / 25.05 / 42.46) | Effectively 100% of the cost |
| **G2P share of total** | **0.04%** | — |
| **Event loop: baseline heartbeat gap** | **0.0112 s** (10 ms timer) | The loop is healthy when idle |
| **Event loop: max gap during synthesis** | **83.01 s** | **The loop took ZERO turns across 83 consecutive seconds** |
| Heartbeat ticks recorded during those 83 s | **1** | Not "slow" — *stopped* |
| Per-sentence blocking (3 sentences, back to back) | **26.56 s / 20.26 s / 36.18 s** | Each one blocks the whole loop |
| **Concurrency test**: `play` alone / `prefetch` alone | 24.56 s / 23.32 s | — |
| **Concurrency test**: both started together (`asyncio.gather`) | **47.88 s** | — |
| **`overlap_ratio`** | **1.000** | **Playback and prefetch are perfectly serialized — prefetch steals wall-clock from playback one-for-one, with zero parallelism** |

**Caveats, stated plainly.** The blocking script drew its 3 sentences from the first 400 sentences of
`cleancodebook.pdf` (stride-sampled), which includes long front-matter lines, so its absolute per-sentence
times (20–42 s) are higher than the 30-sentence whole-book sample in §1.3 (mean 9.55 s). **The absolute
seconds are not comparable across the two scripts; the ratios are.** The 83 s "max gap" is three
consecutive sentences run without an intervening `await` — per sentence, the honest figure is the
20–36 s column.

**Two conclusions follow directly, and they are the backbone of the recommendation:**

1. **G2P is 0.04% of the cost; the torch forward is ~100%.** PyTorch releases the GIL during C++ kernel
   execution, so moving synthesis to a worker **thread** will genuinely unblock the event loop, and
   there is almost no GIL-bound Python left to fight over. A separate **process** is unnecessary.
2. **Prefetch and playback are exactly serialized (`overlap_ratio` = 1.000).** The 50-sentence prefetch is
   not "free background work" — every second it spends synthesizing is a second of latency added to the
   sentence the user is waiting for.

### 1.5 GPU time — ESTIMATED, no GPU measurement possible here

The closest cited same-architecture datapoint is a **Tesla T4, also compute capability 7.5**:
**PyTorch CUDA ≈ 36× realtime** (i.e. ~0.028× RTF), versus ~20× for ONNX Runtime CUDA on the same
hardware — [efemaer benchmark gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653).

T600 Laptop vs T4 (**CITED** specs: T600 Laptop = 896 shaders / 14 SMs / SM 7.5 / 1.41 GHz / 1 MB L2 /
4 GB 12 GHz 128-bit — [SiSoftware](https://ranker.sisoftware.co.uk/show_run.php?q=c2ffc8fcdabbdae7d1e7d4e7d6f082bf8fa9cca994a482f1ccfe&l=zh); T4 = 2,560 cores / 8.1 TFLOPS FP32 / 320 GB/s):

| Ratio | T600 ÷ T4 |
|---|---|
| FP32 throughput | 2.53 ÷ 8.1 ≈ **0.31×** |
| Memory bandwidth | 192 ÷ 320 ≈ **0.60×** |

**INFERRED:** Kokoro-82M is a *small* model whose forward pass is dominated by the iSTFT/vocoder
(≈71% of forward-pass time per the source analysis in §5), so it is bandwidth/launch-overhead bound
rather than FLOP bound. Scaling 36× realtime down by the bandwidth ratio (0.60×) to the compute ratio
(0.31×) gives:

**T600 estimated ≈ 11×–20× realtime** (≈ 0.05–0.09× RTF, i.e. **0.5–1.3 s per 6.3 s sentence**).

**Counter-evidence, and why this estimate is the weakest number in this report.** A directly relevant
4 GB-VRAM datapoint exists and is far more pessimistic: the ebook2audiobook maintainer reports that a
**5-hour audiobook takes "around 5–6 hours" on a 4 GB VRAM NVIDIA card** — i.e. ≈ **1× realtime**, and
"around 7–8 hours" on a desktop i9 CPU
([Discussion #19](https://github.com/DrewThomasson/ebook2audiobook/discussions/19#discussioncomment-10879846)).
Two reasons this does not directly contradict the T4 scaling, but *does* cap my confidence:

1. That figure is for **XTTSv2**, the project's default engine — a substantially larger *autoregressive*
   model, not Kokoro-82M. Kokoro is non-autoregressive and roughly an order of magnitude cheaper per
   second of audio. The same source's own numbers are non-monotonic (4 GB **slower** than 12 GB by more
   than the hardware gap explains), which suggests an old/slow card rather than a clean VRAM scaling.
2. The T4 gist is a single user's benchmark, engine and settings not fully specified.

**Honest range for the T600, therefore: ~1× to ~20× realtime** — the plausible band spans a 20-fold
range, and **only a measurement on the actual machine can narrow it.** The dependent conclusion (below)
is reported as a range rather than a point estimate, and the recommendation does **not** hinge on it.

*Also unverified:* the widely-quoted "2–3 GB VRAM" figure for Kokoro traces to a marketing blog that
labels its own RTF table "estimates".

**Independent sanity check on my own CPU measurement:** Google's LiteRT build publishes on-device
figures for Kokoro-82M on a **Pixel 8a (Tensor G3), CPU, fp32, 4 threads: RTF ≈ 1.8 end-to-end**
([litert-community commit](https://huggingface.co/litert-community/Kokoro-82M/commit/325fa6eb4c9cd7b96b5481976c5bd91ffeea40d5)).
My measured **1.77** on an i7-11850H (§1.3) landing within 2% of a phone-class CPU is a good sign that
the CPU path is measured correctly and that Kokoro's CPU cost is genuinely hardware-insensitive at this
scale — it is a floor imposed by the model, not by this laptop.

### 1.6 Disk available

```
$ df -h .
Filesystem                   Size  Used Avail Use% Mounted on
/home/christia50/.Private     937G  361G  529G  41% /home/christia50/Repos/ebook-reader
```

**529 GiB / 568 GB free** (VERIFIED). Note this is *the entire home filesystem*, shared with everything
else the user stores — not a dedicated app budget.

### 1.7 UI catalog — what "every speed × voice" actually means

**VERIFIED** in source:

- **Speeds actually offered in the reading UI**: `frontend/src/lib/components/MediaBar.svelte:24`
  → `const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]` — **7 discrete values**, not 11.
  `frontend/src/lib/stores/reader.ts:54` clamps to `Math.max(0.5, Math.min(speed, 3.0))`, so the model
  permits continuous 0.5–3.0 but the UI exposes only those 7 buttons. I compute both 7 and the brief's
  11-value grid.
- **Voices**: `backend/routers/voices.py` `ENGLISH_VOICE_CATALOG` lists **28 built-in voices**
  (11 `af_*`, 9 `am_*`, 4 `bf_*`, 4 `bm_*`) plus unlimited user-uploaded `custom:*.pt` voices.
- The MP3 export page (`routes/mp3/+page.svelte:19`) offers a *different* 6-value list
  `[0.5, 0.75, 1.0, 1.25, 1.5, 2.0]` — an existing inconsistency worth noting.

---

## 2 · The arithmetic

### 2.1 Audio duration per sentence: two estimates

| Estimate | Audio seconds per sentence | Basis |
|---|---|---|
| **A — DB blend (MEASURED)** | **3.70 s** | `AVG(duration_ms)` over all 4,553 cached rows |
| **B — bench at 1.0× (MEASURED)** | **7.09 s** | 12.06 chars per audio-second measured at `speed=1.0`; 85.5 chars ÷ 12.06 |

Estimate B is the honest one for a *1x* book. Estimate A implies the existing cache rows were
synthesized at an **average effective speed of 1.92×** (`7.09 ÷ 3.70`) — consistent with a user who
reads fast, and consistent with the fact that speed is not recorded so I cannot prove it.

Both are carried through, as a **lower bound (A)** and a **1x reference (B)**.

### 2.2 Disk cost of full pre-synthesis

Constant: **48,000 bytes per second of audio** (VERIFIED, §1.1).
N = **5,890** speakable sentences per 300-page book.
Speed scaling: audio duration scales as `1/speed` (Kokoro's native `speed` parameter, `CLAUDE.md` hard rule).

**One book, one (voice, speed) at 1x:**

| | Calculation | Result |
|---|---|---|
| A (DB blend) | 5,890 × 3.70 s × 48,000 B | 1,045,945,833 B = **0.97 GiB** (1.05 GB) |
| B (1x reference) | 5,890 × 7.09 s × 48,000 B | 2,005,253,508 B = **1.87 GiB** (2.01 GB) |

**One book, every speed, one voice.** Sum of `1/speed` over the offered values:

- 7 UI speeds: `1/0.5 + 1/0.75 + 1/1.0 + 1/1.25 + 1/1.5 + 1/2.0 + 1/3.0` = **6.6333×**
- 11-value 0.25 grid: `+1/1.75 + 1/2.25 + 1/2.5 + 1/2.75` = **8.4128×**

> **MEASURED correction — `1/speed` is an idealisation, and the real factor is ~2% worse.**
> Kokoro does not deliver the requested speedup. `model.py:108-109` (**VERIFIED in the installed
> package**) is:
> ```python
> duration = torch.sigmoid(duration).sum(axis=-1) / speed
> pred_dur = torch.round(duration).clamp(min=1).long().squeeze()
> ```
> Every token keeps **at least one frame (600 samples = 25 ms at 24 kHz)** *after* the division, so
> compression saturates: the audio cannot get shorter than `tokens × 25 ms`. Measured on
> `voice="af_heart"`, `speed=1.0` as the baseline (`backend/venv/bin/python`, same `KPipeline` config as
> the app):
>
> | Requested speed | Ideal compression | **Achieved — 210-char sentence** | **Achieved — 47-char sentence** | Ideal bytes factor | **Actual bytes factor (85-char avg, interp.)** |
> |---|---|---|---|---|
> | 1.5× | 1.50× | 1.596× | 1.492× | 0.667 | 0.645 |
> | 2.0× | 2.00× | 1.876× | 1.778× | 0.500 | 0.546 |
> | 3.0× | 3.00× | **2.275×** | **2.146×** | 0.333 | **0.460** |
>
> **At 3× speed the audio is only ~2.2× shorter, not 3× — you save 54% of the bytes, not 67%.** The
> effect is strongest on *short* sentences, which is this corpus's common case (average 85.5 chars).
> Summing the measured factors for the 7 UI speeds gives **6.784×** rather than the ideal **6.633×** — a
> **+2.3%** correction. All disk totals below are therefore **lower bounds**; the corrected 7-speed
> figures are given in the table's second column. This is a small correction to the arithmetic but an
> important one to the *decision*: **raising speed barely buys back disk**, which is the opposite of the
> intuition a "speed change is cheap" design would rest on.

| Scope | A (DB blend) | B (1x reference) |
|---|---|---|
| 1 book × 1 speed × 1 voice | 0.97 GiB | 1.87 GiB |
| 1 book × **7 speeds** × 1 voice | **6.46 GiB** (measured-corrected: **6.61 GiB**) | **12.39 GiB** (corrected: **12.67 GiB**) |
| 1 book × **11 speeds** × 1 voice | **8.20 GiB** (corrected ≈ **8.41 GiB**) | **15.71 GiB** (corrected ≈ **16.11 GiB**) |
| 1 book × 7 speeds × **28 voices** | **180.93 GiB** (corrected: **185.08 GiB**) | **346.86 GiB** (corrected: **354.81 GiB**) |
| 1 book × 11 speeds × 28 voices | **229.46 GiB** (corrected: **235.48 GiB**) | **439.92 GiB** (corrected: **451.08 GiB**) |
| **7 books** × 7 speeds × 28 voices | **1,266.48 GiB** (corrected: **1,295.56 GiB**) | **2,428.05 GiB** (corrected: **2,483.67 GiB**) |

**Against 529 GiB free, on the whole filesystem:**

- The full speed × voice matrix for a **single book** consumes **34% (A) to 83% (B)** of all free disk.
- The same matrix for the **7 books in the library** is **2.39× (A) to 4.59× (B) more disk than exists.**
- Even the narrow case — **one book, one voice, all 7 speeds** — is 6.5–12.4 GiB, i.e. **one speed
  change away from ~1–2 GiB of new data, permanently** (no eviction exists; §7).

**This is the finding that decides the question.** Time can be waited out; disk cannot.

### 2.3 Time for full pre-synthesis

**CPU (MEASURED — this is what the app does today whenever CUDA is unavailable):**

```
5,890 sentences × 9.55 s/sentence = 56,250 s = 15.63 hours   (one pass, one voice, one speed)
```

| Scenario | CPU wall clock |
|---|---|
| 1 full-book pass, 1 voice, 1 speed | **15.6 h** |
| 1 book × 11 speed changes, 1 voice | **171.9 h** (~7.2 days) |
| 1 book × 7 speed changes × 28 voices | 15.63 × 6.6333 × 28 = **2,903 h** (~121 days) |

**T600 GPU (ESTIMATED, §1.5) — the honest range is ~1× to ~20× realtime:**

```
optimistic (20× realtime):  5,890 × 7.09 s ÷ 20 = 2,088 s =   34.8 min
central    (11× realtime):  5,890 × 7.09 s ÷ 11 = 3,797 s =   63.3 min
pessimistic ( 1× realtime): 5,890 × 7.09 s ÷  1 = 41,760 s =  11.6 h
```

| Scenario | GPU wall clock (EST.) |
|---|---|
| 1 full-book pass, 1 voice, 1 speed | **35 min – 11.6 h** |
| 1 book × 11 speed changes, 1 voice | **6.4 h – 5.3 days** |
| 1 book × 7 speed changes × 28 voices | **4.5 – 909 days** |

**Honest read:** even at the *optimistic* end, a single GPU pass over one book takes the better part of
an hour, and that is the most favourable number in this document. At the pessimistic end (the only
directly-measured 4 GB-VRAM datapoint available, §1.5), it is an overnight job. **The user's proposal
survives only in the single-pass, single-voice, single-speed case, and only if the GPU measures near the
optimistic end.** Everything downstream fails regardless: 11 speed changes is a working day at best, and
"every speed × voice" is weeks of GPU time for storage that does not fit on the disk.

### 2.4 The existing cache is already the smallest useful slice — and it is 771 MiB for 4.7 hours

4,553 rows / 4.68 hours of audio / 3 voices = **771 MiB**. That is roughly **0.5 book-equivalents** at
1x. Extrapolating the *existing* approach (cache what you actually listen to, forever) to a real
library: reading 10 books at 1x with occasional speed changes is **~10–20 GiB** — annoying but
survivable. That is the scale the current design is at, and full pre-synthesis would move it to
**hundreds of GiB per book**.

### 2.5 Startup latency / UX

**Time-to-first-audio today (VERIFIED from source, INFERRED timing):**
On `play`, `_producer` enqueues sentence `from_index` first (`tts.py:55-65`), the consumer immediately
calls `stream_job`, which on a **cache miss** runs one Kokoro inference before the first chunk is
emitted. So today's worst case = **one sentence synthesis** = 1.9–24.3 s measured on CPU, ~0.5–1.3 s
ESTIMATED on the T600. On a **cache hit** it is near-instant (PCM read + 100 ms WAV chunks).

**Time-to-first-audio with full pre-synthesis:** the user must wait for the **entire book** —
**15.6 h measured on CPU**, 35 min–11.6 h ESTIMATED on GPU — *before any audio plays*. This is strictly
worse in every case except "the user is willing to start listening tomorrow".

**And it cannot honestly be shown as progress that isn't real.** `implementation-handoff.md` §1.7:
*"Never port the prototype's simulations. No fake progress timers, no invented metrics."* §4:
*"Error copy is specific."* A 15-hour job does allow a **real** determinate progress bar — but only if it
reports actual `sentences_done / sentences_total` from the DB, which means the job must be a persisted,
resumable, observable queue item, not a request-scoped `asyncio.Task`. That is real infrastructure:
the repo currently has **no job table, no worker, no resumable queue**. Note it *does* already have a
working precedent — `MP3Export` + `POST /mp3/export` (§7) — which is exactly this pattern, single-book,
with a real `progress` column.

**A subtlety that matters more than the number.** Because `speed` is part of the cache key, the *current*
design already re-synthesizes on a speed change — that is what `prefetch_speed` exists for
(`tts.py:211-229`, `api.ts:281`). So the user is not asking for a new behaviour; they are asking to make
an existing behaviour **eager and total** instead of **lazy and local**. The measurements say: keep it
lazy, make it local *and responsive*.

---

## 3 · Comparison table

Scored for **this** app: local-only, Kokoro-82M, T600 4 GB / i7-11850H, 529 GiB free, SQLite cache.

| Dimension | **A · Dynamic on-demand** (today) | **B · Full-book pre-synthesis** | **C · Hybrid** (recommended) |
|---|---|---|---|
| **Time to first audio** | 0 s cache hit; **1.9–24.3 s measured** CPU / ~0.5–1.3 s EST. GPU on miss | **15.6 h measured** CPU / 35 min–11.6 h EST. GPU, once per book — *before anything plays* | Unchanged from A (starts on demand) |
| **Steady-state smoothness / lag risk** | **Poor today, fixable.** The synthesis call blocks the event loop for the whole sentence, so playback stutters whenever the prefetcher loses the race — which on CPU it always does (RTF 1.77 > 1.0) | Smooth **once built**; but every speed/voice change reintroduces the full stall | **Good**: same smoothness as B for the warmed window, with A's instant start |
| **Total wall clock** | Only what is actually listened to | 15.6 h/book CPU; 172 h per 11 speed changes; days per full matrix | ≈ A, plus bounded idle-time warm-ahead |
| **Disk** | What you listen to: **771 MiB for 4.7 h** across 3 voices. Unbounded growth, no eviction | **185–355 GiB per book** (7 speeds × 28 voices); **1.30–2.48 TiB for 7 books** vs **529 GiB free** → **does not fit** | Bounded by an explicit cap (e.g. 2–4 GiB) with LRU eviction |
| **RAM / VRAM** | Peak = one sentence's audio (≤1.24 MB PCM) + model. **VRAM is the real 4 GB risk** (and overflow is *silent*, not fatal): cited 2.37 GB host+CUDA-context floor and 3.11–3.98 GB loaded, both measured on WSL2 and probably pessimistic natively | Same working set, but a long-running job holds the CUDA context *and* desktop compositor contention for hours | Same as A; batch size 1 keeps VRAM flat |
| **CPU/GPU contention** | Prefetch and playback **serialize on one event loop** and fight for the same GPU/CPU | **Worse**: hours of sustained 100% GPU/CPU while the user wants to use the machine. Cited practice: concurrency must be *limited to keep a shared GPU usable* ([libratory](https://github.com/subev/libratory)) | One serialized worker; prefetch yields to playback |
| **Battery / thermals** | Bursty, short | **Sustained 15.6 h CPU (or 35 min–11.6 h GPU) at full tilt** per book; a T600 laptop under sustained load thermally throttles, lengthening the job | Bursty; idle-timer work only when plugged in |
| **Complexity** | Already built | New: persistent job queue, resumable checkpoints, progress API, cancellation, eviction, migration | Small: thread offload + time-based prefetch window + eviction |
| **Failure modes** | Stutter; unresponsive server; pause/seek latency up to a full sentence | Job killed at 90% loses everything (no checkpoint); disk fills mid-job; DB grows unbounded; OOM on 4 GB VRAM; **and the user still can't listen while it runs** | Worker crash loses one sentence; disk capped by design |
| **Resilience to speed/voice switching** | New key → re-synthesize; playback continues from cache while `prefetch_speed` warms the new key (`audio.ts:301-321`, 100 ms debounce) | **Catastrophic**: every switch = full-book rebuild, hours/days, and a fresh multi-GiB copy per (voice, speed) | Switch is cheap: re-synthesize the live window; warm the rest only if the setting sticks |
| **Verdict** | Keep, but **fix the blocking** | **Reject** — fails on disk by 2.5–4.7× | **Adopt** |

---

## 4 · What comparable products actually do

**Read this caveat before the table.** Almost every commercial product here is a **cloud** service
synthesizing on a **fleet of server GPUs**. Their economics — elastic compute, no local disk, metered
per character — are the *inverse* of a 4 GB laptop running the model itself. "Speechify streams on
demand at 56 ms" is therefore **not** evidence that a local app can stream on demand at any particular
speed. Specifically **not apples-to-apples**:

- **Server GPU farm vs 4 GB laptop GPU.** Speechify's "56 ms p50 first byte" is measured on their
  production path in US-East; ElevenLabs runs production endpoints in four regions. Neither is a target
  a T600 can be held to.
- **"Offline" means three different things** across these products, which breaks naive comparison:
  (a) a genuinely **on-device model** (Speechify's on-device engine, Thorium/calibre system voices);
  (b) **pre-fetched server-rendered audio with a lease** (ElevenReader offline, Speechify iOS);
  (c) a **user-initiated file export** (NaturalReader MP3, Speechify web MP3). Only (a) is
  architecturally local. **The user's "precompile" proposal is (a)-class work with (b)-class storage.**
- **The latency metrics are different metrics.** Speechify reports *time-to-first-byte* (56 ms);
  ElevenReader reports *time-to-complete-download* (2–5 min); ebook2audiobook reports
  *time-to-complete-book* (hours). Tabulating them together is meaningless.

| Product | On-demand vs pre-generate | Where | Voice/speed change → regenerate? | Citable numbers |
|---|---|---|---|---|
| **NaturalReader** | **Hybrid, cloud-first.** Live playback = metered on-demand cloud; "Convert to mp3" = explicit user-triggered batch | **Cloud** (AI voices on Gemini/ChatGPT/Azure); on-device only for free OS voices | MP3 bakes in voice+speed at conversion time | **20 pages / 50,000 chars per MP3 job; 1M chars/mo; MP3s stored 30 days**; 20,000 chars/day free tier |
| **Speechify** | **Hybrid, dual-engine, user-selectable.** Cloud by default; a real **on-device local model** for offline. API documents **both** batch and stream endpoints | **Both** — "Text to Speech Engine" dropdown (Cloud \| On-Device) | iOS: "playback will instantly switch to the new voice" ⇒ live re-synthesis, not a pre-rendered file | 56 ms p50 / 102 ms p90 first byte; batch endpoint caps at 2,000 chars, stream at 20,000; own blog recommends `hash(chunk) → cached MP3` |
| **ElevenLabs Reader (ElevenReader)** | **Server-side generate-on-first-listen, then replay from cache.** Offline = explicit forward pre-generation | **Cloud** | Not documented for Reader | Offline prep **2–5 min**; **20 h/day + 150 h/month** offline cap; downloads **expire after 60 days** |
| **ElevenLabs Studio Audiobooks** | **Pure pre-generation** with paragraph-level cache — *and it documents the answer to this report's question* | **Cloud** | **Yes: "Existing paragraphs will not update automatically. You must regenerate audio for changes to take effect, which will use credits."** | Two playback modes: *"generate one at a time"* vs *"generate clips ahead"* |
| **ebook2audiobook** | **Pure pre-generation. No streaming code path exists.** Not a player — a one-shot converter | **Local** (CPU/CUDA/ROCm/MPS) | **Yes** — key = `block_hash(text, voice, tts_engine, ...)`. **Speed is NOT in the key** → mixed-speed books on resume | 5 h audiobook: **5–6 h on 4 GB VRAM**, 4–3 h on 12 GB, 7–8 h desktop i9 CPU, "**like 5 days**" laptop CPU. **No ETA feature exists.** |
| **Alexandria** | **Local batch**, multi-voice, selective per-chunk regeneration | **Local** (Qwen3-TTS) | Selective manual regeneration | "**3–6× real-time**" (vendor claim); **8 GB VRAM minimum** — 2× the T600 |
| **⭐ Recite** | **Hybrid — the closest existing analogue to this report's recommendation.** "Read now, generate ahead": lane 1 synthesizes near the cursor, lane 2 renders the rest in background; **narration starts while later parts are still rendering** | **Local**, offline **Kokoro-82M** | Not documented | `RECITE_TTS_WINDOW_CHUNKS=80` ("audio kept ready past the cursor"), 6 workers, per-book audio + SQLite, SSE `pending → synthesizing → ready` |
| **Thorium Reader** | **On-demand stream** | **On-device** (Chromium **Web Speech API**, not Piper — a Piper integration is a proposal only) | Re-synthesized live; **one utterance in flight** | Speed ×0.5–×2; word underline + sentence highlight ⇒ real-time streaming |
| **calibre** | **Hybrid — and a stronger precedent for pre-compilation than ebook2audiobook.** Live `Read aloud` in the viewer, **plus** a whole-book `embed_tts()` tool that bakes per-sentence WAV + SMIL timing **into the EPUB itself** | **On-device**, 6 engines, "**local only**… no data is sent to any cloud servers" | Re-synthesized live in the viewer | Speed ×0.5–×2 live; 22,050 Hz export. **No audio cache at all — only a model cache** ([new-in/seventeen](https://calibre-ebook.com/new-in/seventeen)) |
| **⭐ OpenWebTTS** | **Hybrid: per-chunk pre-generation into a persistent content-addressed cache** — the closest OSS realisation of the design this repo already has | Either (Piper/Kokoro/Coqui/Kitten local; Gemini/OpenAI cloud) | **Voice and engine are in the key, speed is not**: `sha256(f"{text}-{voice}-{engine}")` → `{hash}.wav`; client polls `HEAD` every 2 s for `ready`/`generating` | Exposes `/api/clear_cache` and `/api/cache_size` ([repo](https://github.com/Gyyyn/OpenWebTTS)) |
| **Aperture** | **Pure on-demand, in-memory, no cache at all** | Local Kokoro (PyQt6) | Re-synthesized live | `queue.Queue(maxsize=10)` — a **bounded buffer proving backpressure** ([repo](https://github.com/rudra-mondal/aperture-epub-reader)) |
| **Readium Speech Server** | **On-demand with an explicit prefetch window** | Either (**PocketTTS local**, ElevenLabs cloud) | Re-synthesized live | **`prefetchWindow = 3`**, and "requests are chained one at a time, **never more than one `/synthesize` in flight**"; `readyBufferChars = 400`. Documents that it is "**missing key features such as caching**" ([repo](https://github.com/readium/speech-server)) |
| **Koodo Reader** | **On-demand, one audio file per utterance, with explicit lookahead** | Either (plugin decides); `system` voices bypass the cache entirely | **Speed is an explicit synthesis argument**: `getAudioPath(text, speed, dirPath, config)` | `cacheAudio(index, speed, …, 10, true)` blocking, then `cacheAudio(index+1, speed, …, 20, false)` non-blocking |
| **repy** | **On-demand per-sentence + prefetch** | Either (`purr`/KittenTTS local default; edge-tts/Google cloud) | Speed passable via engine template | "Text is sent to the TTS engine in manageable chunks (**sentence-by-sentence**)"; positional keys `repy_tts_{index}.mp3`, temp dir removed on stop |
| **This repo (Kokoro Reader)** | **On-demand per sentence + 50-sentence prefetch** | **Local** (CUDA or CPU) | Yes — `SHA256(text:voice:speed)` | See §1 |

### The decisive datapoint: NaturalReader caps batch generation hard

NaturalReader — a **cloud** product whose marginal GPU-second is near zero — lets you convert to MP3,
and then: **20 pages at a time** ("The 20-page limit applies per conversion, not per document. Larger
files can be converted in parts."), **50,000 characters** for typed text, **1 million characters per
month**, and **MP3s are stored for 30 days**.
([source](https://help.naturalreaders.com/en/articles/11543218-working-with-text-and-audio-personal-version))

A 300-page book is ~463,000 characters. **Full-book pre-generation is therefore impossible in
NaturalReader in one action even in the cloud**, and the monthly allowance is ~2 books. A company that
could trivially afford to pre-generate whole books chooses not to. Their **30-day expiry** is also the
closest thing to a cache eviction policy found in any product here — a direct counterexample to this
repo's unbounded cache (§7).

### ElevenLabs documents the exact failure mode the user is proposing

ElevenLabs Studio's audiobook product is the clearest published pre-generation system, and it states the
regeneration cost plainly: *"If you change voice or model settings after generating audio: Existing
paragraphs will not update automatically. **You must regenerate audio for changes to take effect, which
will use credits.**"* Playing already-generated audio is free; **changing the voice is not.**

Its mitigation is precisely the hybrid this report recommends — two playback modes, *"generate one at a
time"* versus *"**generate clips ahead** — plays from the selected paragraph to the end of the chapter,
generating multiple paragraphs ahead for smoother playback"* — with per-paragraph state shown as a
**dark bar (generated) / light grey bar (not yet generated)**, *not* an ETA.
([source](https://elevenlabs.io/docs/eleven-creative/products/audiobooks))

Note the shape of that UI: **per-chunk state, not a percentage and not a time estimate.** That is
exactly the honest progress the handoff's §1.7 permits, and it is what a local implementation should copy.

### Where a local app *does* pre-generate — and what it costs

**ebook2audiobook** is the reference local pre-generator, and its architecture is instructive: sentence →
**FLAC file on disk** → ffmpeg concat per chapter → concat+mux per book. There is no streaming path. Its
`--session` flag exists "to resume the conversion in case of interruption, crash" — i.e. **the authors
treat a lost multi-hour job as a first-class problem**, which is exactly the failure mode §3 flags for
strategy B. It reports progress as `NN.NN%` plus *the sentence currently being synthesized*, and
**contains no throughput-based ETA anywhere** (grep for `remaining|eta|estimat|time_left` → nothing).
It **declined to publish benchmarks**: *"too much work for not enough user amount interest. closing."*
([repo](https://github.com/DrewThomasson/ebook2audiobook),
[Discussion #19](https://github.com/DrewThomasson/ebook2audiobook/discussions/19#discussioncomment-10879846),
[Discussion #348](https://github.com/DrewThomasson/ebook2audiobook/discussions/348#discussioncomment-15279881)).
Its cache key includes voice but **not speed** — code-verified — so changing speed and resuming produces
a **mixed-speed book**. This repo's `SHA256(text:voice:speed)` is *stricter than every product surveyed*
and should stay that way.

**Libratory** is the closest analogue in spirit — it uses **Kokoro-82M**, runs locally, and is a real
library app. It resolves the same tension three ways, each of which validates the hybrid:

1. **It pre-generates per *chapter*, not per book** — small enough to resume, small enough to discard.
2. **It explicitly caps concurrency to keep the machine usable**: *"Settings sets how many jobs each pool
   runs at once, **within limits that keep a shared GPU usable**."*
3. **It evicts**: *"once it is written, the worker **deletes the intermediate chunk WAVs to reclaim
   disk** (`pnpm --filter server cleanup:chunks` sweeps leftovers from older runs)."*
   ([repo](https://github.com/subev/libratory))

**And directly on point for the user's speed request:** Libratory's **local model narrators run at fixed
speed and the UI disables the slider** (only macOS system voices and cloud voices support speed). A
mature local TTS library app looked at "user changes speed ⇒ all audio is invalid" and **removed the
feature** rather than pay the regeneration cost.

**⭐ Recite** is the strongest validation of this report's recommendation, because it is a shipped local
**Kokoro-82M** reader that already implements the hybrid: *"Read now, generate ahead — sections near your
position synthesize first (lane 1) while the rest of the book renders in the background (lane 2);
**narration starts while later parts are still rendering**."* Its tuning knob is exactly the fix
recommended in §6 Phase 2 — `RECITE_TTS_WINDOW_CHUNKS=80`, described as *"audio kept ready past the
cursor"* — and its progress is reported over SSE as a **three-state enum, `pending → synthesizing →
ready`**, not a percentage or an ETA. ([repo](https://github.com/SAIL0R34/Recite))

### The consistent industry pattern

Every product surveyed — cloud or local, reader or batch renderer — uses the **same** core design:
**split into chunks → synthesize per chunk → cache keyed on the chunk → concatenate.** Speechify's own
engineering blog recommends exactly this, including *"Hash each chunk's input, key the cached MP3 by that
hash, reuse on a hit"*
([Speechify](https://speechify.ai/blog/building-an-automated-audiobook-pipeline-with-the-speechify-tts-api)).
**The differentiator is never the architecture — it is *when* the cache fills.** This repo's
`AudioCache` + `SHA256(text:voice:speed)` is already the industry-standard design. The question is not
whether to replace it; it is how to fill it in the right order.

**Progress-UX convention across all of them:** nobody publishes a time-based ETA. ebook2audiobook has
none by design; ElevenLabs uses per-paragraph dark/light bars; Recite uses a 3-state SSE enum. This is
independent confirmation that the handoff's "no invented metrics" rule is not a local idiosyncrasy but
the norm.

### This repo's cache key is ahead of the entire field — keep it

Reviewing every product and OSS reader surveyed, a consistent (and consistently *wrong*) pattern emerges:
**voice is treated as part of a chunk's identity, and speed is treated as a render option.**

| Implementation | Cache key | Speed included? |
|---|---|---|
| **This repo** | `SHA256(text:voice:speed)` | **Yes** |
| OpenWebTTS | `sha256(f"{text}-{voice}-{engine}")` | No |
| ebook2audiobook | `block_hash(text, voice, tts_engine, fine_tuned, sentences)` | **No** |
| Koodo | positional `{index, audioPath}`, filename = timestamp | No (but passed as a synthesis arg) |
| repy | positional `repy_tts_{index}.mp3` | No |
| Readium Speech Server | *(no cache — "missing key features such as caching")* | — |

**No implementation found anywhere puts `speed` in the cache key.** The demonstrated failure mode of
omitting it is concrete: ebook2audiobook's own code prints a warning about reconversion on *voice*
change but **silently produces a mixed-speed book** when resuming after a speed change — code-verified.
This repo's key is therefore not over-engineering; it is the correct design, and §5.2's observation that
`prefetch_speed` exists at all (`tts.py:211-229`) shows the maintainers already understood this.

### The scheduler design space, and where this repo should sit

Two shipped implementations bracket the concurrency decision:

| | **Readium Speech Server** | **Recite** |
|---|---|---|
| Concurrency | **One `/synthesize` in flight, ever** | **6 concurrent Kokoro pipelines** |
| Look-ahead | `prefetchWindow = 3` utterances | `RECITE_TTS_WINDOW_CHUNKS = 80` |
| Rationale | shared/remote synthesis endpoint must not be flooded | local GPU can absorb parallel work |

**This repo belongs on the Readium end, deliberately** — for the reason §5.2 measures: on a CPU that is
1.8× too slow, concurrency buys nothing (the work is serial anyway) and costs event-loop responsiveness.
Recite's 6-way concurrency presumes a GPU with headroom; a 4 GB T600 shared with the desktop compositor
does not have it. **Start at exactly one worker; raise it only against a measurement.**

**Note the one genuinely different precedent: calibre.** Its `embed_tts()` tool pre-compiles a whole book
— but the artifact is **per-sentence WAV plus SMIL timing baked into the EPUB**, not a private cache.
That is a *portable read-along document*, i.e. a **user-facing export** (like this repo's
`POST /mp3/export`), not an internal speed-up. It is a good model for what a "precompile" feature should
produce if one is ever built: something the user **owns and can take elsewhere**, which justifies the
disk cost in a way a hidden cache never can. Tellingly, calibre keeps **no audio cache at all** — only a
model cache.

---

## 5 · Hardware analysis: what actually makes this laptop lag

This is the part of the user's premise that is *correct in symptom and wrong in mechanism*. Pre-compiling
would hide the symptom by moving the work earlier; it would not fix the cause, and it introduces worse
ones. Here is the precise mechanism.

### 5.1 The real cause: `_call_kokoro` blocks the asyncio event loop for a whole sentence

**VERIFIED by reading source.** `KPipeline.__call__` is a **generator**; the work happens on `next()`:

```python
# backend/services/tts_engine.py:63-76
def _call_kokoro(self, text, voice, speed):
    result = self.kokoro(text, voice=voice, speed=speed)   # cheap: builds a generator
    return result                                          # returns the un-consumed generator
```

```python
# backend/services/tts_engine.py:162-168  (stream_job)
for result in results:          # <-- next(results) runs the ENTIRE inference, synchronously
    await asyncio.sleep(0)      # <-- yields only AFTER the inference has finished
```

```python
# backend/services/tts_engine.py:220-225  (prefetch)  — note: no await inside the loop at all
for result in results:
    if cancel.is_set():
        return
    _, audio_offset = self._collect_result(result, audio_parts, word_timestamps, audio_offset)
```

`kokoro/pipeline.py:351` is `def __call__(self, text, voice='af_heart', speed=1, split_pattern=r'\n+')`.
Because each job is **one sentence**, `split_pattern` never fires and the generator yields **exactly one
result** — so a single `next()` performs the whole inference, and the `await asyncio.sleep(0)`
immediately after it yields only once the damage is done. **There is no `await` inside `prefetch`'s
loop whatsoever.**

**Consequence (INFERRED, mechanically):** while a sentence is being synthesized, the single-threaded
asyncio event loop cannot run *anything* else — not the WebSocket receive path, not `cancel.is_set()`
checks in another task, not `/health`, not any HTTP endpoint. On this CPU that window is **1.9–24.3 s
measured (mean 9.55 s)**, and `/health` is unresponsive for its duration. A pause or seek pressed
mid-sentence cannot take effect until the sentence finishes.

This is not the GIL. PyTorch releases the GIL during C++ kernel execution, so a *separate thread* would
help; a coroutine on the *same* thread cannot. **The blocker is the event loop, not the interpreter lock.**

### 5.2 Why the prefetch makes it worse instead of better

`tts.py:206-209` warms **50 sentences** on every play/seek:

```python
prefetch_task = asyncio.create_task(
    engine_tts.prefetch(sentence_data, from_index + 1, 50, voice, speed, prefetch_cancel))
```

At the measured RTF of **1.77×**, 50 sentences ≈ 185 s of audio (using 3.70 s/sentence) but **478 s of
generation** (50 × 9.55 s). Playback consumes those 185 s of audio in 185 s. **The prefetcher falls
behind by ~293 s and can never catch up.** It therefore runs *continuously and permanently in the
background*, and each of its iterations blocks the event loop for a full sentence.

**That is the lag the user is feeling.** It is not a lack of pre-synthesis — it is an *unbounded,
always-behind* prefetch job monopolising a single-threaded server, on a machine where synthesis is
1.77× slower than playback. Doubling down with *more* pre-synthesis (whole book) would make the
monopolisation permanent and total.

Two further VERIFIED details compound it:

- **Prefetch and playback duplicate work.** `_producer` enqueues from `from_index` forward
  (`tts.py:55-65`) while `prefetch` starts at `from_index + 1` and walks the *same* sentences
  (`tts.py:200-232`). Both check the cache first (`tts_engine.py:134-135`, `:209-210`) and both write on
  a miss (`:179-181`, `:227-229`) — so the same sentence can be synthesized twice concurrently.
- **The producer can queue 30 jobs** (`asyncio.Queue(maxsize=30)`, `tts_engine.py:32`) regardless of how
  long they take, so the queue depth carries no back-pressure information about the real rate.

### 5.3 Why GPU helps and why it is not free

Synthesis on the T600 would cut the blocking window from ~9.5 s to a **sub-second** ESTIMATED figure,
which is the single biggest available win — it is the *fix*, not the workaround. But:

- **4 GB VRAM is genuinely tight, and its failure mode is silent.** Cited figures: a bare Kokoro
  pipeline at **0.8–1.6 GB**, a **2.37 GB host + CUDA context floor**, and **3.11 GB (short) / 3.98 GB
  (long-form) loaded** — but those were measured on Windows+WSL2, which carries ~1.3 GiB of invisible
  reserve, so native Linux/Windows probably lands lower and **probably fits**. Nobody has measured it.
  **The dangerous part is that overflow is not an error:** NVIDIA's sysmem fallback (driver 536.40+)
  makes an over-budget allocation spill to system RAM and *succeed*, producing 10–100× slowdown with
  **clean logs and `cudaMalloc` returning success**
  ([Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI),
  [issue #15](https://github.com/remsky/Kokoro-FastAPI/issues/15)). Set
  **CUDA → Sysmem Fallback Policy → Prefer No Sysmem Fallback** so an overflow is a visible OOM instead
  of an inexplicable slowness.
- **fp16 is not the answer — and on this card there is no fp16 fast path at all.** The T600 Laptop is a
  **TU117 die (GTX 1650-class) and has *no tensor cores*** ([NotebookCheck](https://www.notebookcheck.net/NVIDIA-T600-Laptop-GPU.555220.0.html);
  Wikipedia's Turing die table lists Tensor cores "N/a" for TU116/TU117), and Turing has **no BF16**.
  So fp16 could only ever run on plain CUDA cores, and there it measurably *loses*: on an RTX 3060 Ti over
  ~22 s of speech, fp32 **0.48 s** vs fp16 **0.63 s** vs int8 **9.92 s**
  ([kokoro-onnx #112](https://github.com/thewh1teagle/kokoro-onnx/issues/112)). There is also an unmerged
  report that Kokoro's SineGen phase accumulator degrades badly in fp16 (correlation vs fp32 falling to
  0.006 within 10 s) ([hexgrad/kokoro #353](https://github.com/hexgrad/kokoro/pull/353)).
  **Run fp32, and if you want the memory win use fp16 *storage* with fp32 *compute* (325→162 MB,
  bit-identical audio).** Quantization buys memory, never speed — int8 was slower than fp32 in *every*
  measurement found, by up to 2.9×.
- **The real desktop-contention mechanism is CUDA context loss, not TDR.** TDR is close to a non-issue
  (NVIDIA's own guidance allows kernels >2 s on WDDM2 without a TDR, and Kokoro's kernels are
  millisecond-scale). But **mode switches — resolution change, fullscreen DirectX, Alt+Tab,
  Ctrl+Alt+Del — can invalidate the CUDA context outright**, after which subsequent calls fail with an
  invalid-context error (CUDA Programming Guide §6.5). Any GPU path must therefore treat context loss as
  **recoverable and fall back to CPU**, not as a crash.
- **ONNX is not automatically faster than PyTorch on Turing.** Same-architecture (T4, cc 7.5) figures:
  PyTorch CUDA **36×** realtime vs ONNX CUDA **20×**
  ([gist](https://gist.github.com/efemaer/23d9a3b949b751dde315192b4dcf0653)).
- **A 4 GB laptop GPU running a multi-hour job is a bad citizen.** Sustained CUDA load heats a thin
  chassis, and the desktop compositor shares the device. Libratory's explicit decision to cap job
  concurrency "within limits that keep a shared GPU usable" is the right prior art.

### 5.4 Disk I/O — a real but secondary term

`stream_job` yields one WAV per 100 ms of audio (`tts_engine.py:160`, `SAMPLE_RATE // 10`), each a fresh
`io.BytesIO` + `soundfile.write` + `websocket.send_bytes`. For a 6.3 s sentence that is ~63 tiny
allocations and full WebSocket frames per sentence, with no batching or backpressure. On the read path,
`AudioCache.audio_data` is a BLOB up to 1.24 MB read whole into RAM per sentence. This is real overhead
but it is **not** the dominant term — the dominant term is the 1.9–24.3 s inference holding the loop.
(Bandwidth-wise this is trivial: 48 KB/s of audio is nothing against a 937 GB filesystem. The cost is
syscall/WAV-header churn and await granularity, not throughput.)

Worth contrasting with **Aperture**, a local Kokoro reader that does this correctly: it uses a **bounded**
`queue.Queue(maxsize=10)` between the synthesis producer and the playback consumer
([repo](https://github.com/rudra-mondal/aperture-epub-reader)). A bounded queue provides genuine
backpressure, so a producer that cannot keep up is throttled instead of running away. This repo's
`asyncio.Queue(maxsize=30)` (`tts_engine.py:32`) is bounded too, but the *prefetch* task is not tied to it
at all — which is precisely why prefetch can monopolise the loop (§5.2).

### 5.5 A quality footnote that argues *for* the hybrid

Kokoro's default text splitting is `split_pattern=r'\n+'` with a 510-phoneme hard cap, and
Kokoro-FastAPI warns in both directions: 510-token chunks cause *"'rushed' speech and other artifacts"*,
while *"artifacts in intonation can increase with smaller chunks."* Their tuned sweet spot is
**175–250 phoneme tokens** ([Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)).

This repo synthesizes **one sentence at a time** — roughly 85.5 characters ≈ **90–110 phonemes** — which
sits *below* that tuned band. That is a minor prosody consideration, not the lag cause, but it has a
useful implication for strategy: a hybrid that synthesizes a **small run of consecutive sentences in one
call** would land inside the tuned range *and* amortise the per-call overhead over more audio. A
whole-book batch would do the same thing far more expensively. Note also the documented abbreviation trap
(`etc.`, `e.g.` causing mid-sentence splits and long unwanted pauses,
[issue #308](https://github.com/remsky/Kokoro-FastAPI/issues/308)) — relevant because this repo's
sentences come from spaCy, which handles those correctly, so **keep splitting on sentences and pass runs
of them, rather than switching to a character-count splitter.**

### 5.6 So which strategy actually fixes the lag?

| Claim | Verdict |
|---|---|
| "The laptop lags because it synthesizes during reading" | **Partly true** — but the *mechanism* is event-loop blocking + an always-behind prefetcher, not the mere act of synthesizing |
| "Precompiling the whole book fixes it" | **Fixes the symptom, at a cost that does not fit the disk**, and only for the exact (voice, speed) pair precompiled |
| "Move synthesis off the event loop and bound the prefetch window" | **Fixes the actual cause**, ~10× cheaper, works on CPU *and* GPU |
| "Re-synthesize the book on every speed/voice change" | **Not viable** — 172 h CPU/book/voice and 6.6–12.7 GiB per book *per voice* for 7 speeds, times 28 voices |

---

## 6 · Recommendation for this repo

Phased, smallest-diff-first. Phases 1–2 are each independently shippable and each removes a real cause
of the reported lag.

### Phase 1 — the smallest change that delivers the stability win (~1 file)

**Take Kokoro off the event loop, and serialize synthesis behind a single worker.**

In `services/tts_engine.py`, make the blocking part of `_call_kokoro`/`collect` run via
`await asyncio.to_thread(...)` (Python 3.12, already the runtime) with a **module-level
`ThreadPoolExecutor(max_workers=1)`** so that:

- the event loop stays free for WebSocket frames, `/health`, and cancellation checks during inference;
- `max_workers=1` **serializes** synthesis, so prefetch can never run concurrently with playback and
  torch never oversubscribes threads (a real risk: `torch.get_num_threads()` is 8 here, and two
  concurrent inferences would contend for the same 8 cores and the same CUDA context). This is exactly
  the choice the **Readium Speech Server** made deliberately — *"requests are chained one at a time,
  never more than one `/synthesize` in flight"* ([repo](https://github.com/readium/speech-server));
- `asyncio` cancellation is observed between sentences rather than after a 24 s stall.

**Why a thread and not a process:** PyTorch releases the GIL during C++ kernel work, so a second thread
genuinely runs in parallel with the loop. Only misaki's G2P is pure Python; it interleaves on the 5 ms
GIL switch interval (§1.4's `bench_blocking.py` measurement separates the two). A process
would cost a second copy of the 327 MB model and lose the warm CUDA context — not worth it at this scale.

**Expected effect (INFERRED):** pause/seek latency drops from "up to one full sentence" to "next await
point"; `/health` and all HTTP endpoints stop freezing; the UI stops feeling hung during synthesis.

### Phase 2 — bound the prefetch window by *audio seconds*, not sentence count

Replace `prefetch(..., count=50, ...)` with a **time-budgeted** window: keep synthesizing until
`buffered_audio_seconds >= TARGET` (start at 45–60 s), then **stop and sleep**. Add an explicit
`if playback_needs_the_worker: yield` gate so prefetch never delays a live sentence.

Also remove the duplicate work between `_producer` and `prefetch` — they walk the same indices — by
having the producer be the single source of truth and letting prefetch only look *beyond* the producer's
horizon.

**Expected effect:** on CPU the prefetcher will still not keep up (it cannot — RTF 1.77 > 1.0, and
`playbackRate` is deliberately pinned at 1.0 per `CLAUDE.md`). But it will stop monopolising the server:
it will warm a bounded window, stop, and let the live sentence through. On GPU it will keep up
comfortably.

### Phase 3 — add eviction, which does not exist at all today

This is **orthogonal to the strategy question and should ship regardless**. See §7.

### Phase 4 (optional) — bounded, idle-time warm-ahead, GPU-only, next chapter only

If the user still wants "no waiting", the honest version is:

- **Warm the next chapter only**, not the book: chapters already exist (`Sentence.chapter`,
  `Sentence.chapter_title` — VERIFIED in `db/models.py`), and a chapter is a resumable, discardable unit
  (exactly Libratory's design choice).
- **Only on the GPU path**, and only when `device == "cuda"` and the app knows it (requires the
  capability probe the handoff §9.2 notes is missing).
- **Only on an idle timer** (no WebSocket activity for N seconds), gated on AC power, and **pausable**:
  the moment the user hits play, the worker yields.
- **Bounded by the Phase 3 cap** so it cannot fill the disk.
- **Progress must be real**: per the handoff's no-fake-progress rule, expose a **three-state enum per
  sentence — `pending → synthesizing → ready`** rather than a percentage or a time estimate. Every
  product surveyed does exactly this (§4), and it is the honest representation of a job whose total
  duration is not knowable in advance.

**This phase is not speculative — a shipped local Kokoro reader already does it.** **Recite** implements
"read now, generate ahead": lane 1 synthesizes near the cursor, lane 2 renders the rest of the book in
the background, **narration starts while later parts are still rendering**, and its look-ahead is tuned
by `RECITE_TTS_WINDOW_CHUNKS=80` — *"audio kept ready past the cursor"* — with SSE
`pending → synthesizing → ready` ([repo](https://github.com/SAIL0R34/Recite)). That is Phases 2 + 4 of
this plan, independently arrived at and shipped. It is reasonable to treat Recite as a reference design.

**Do not build** a persistent whole-book job queue, a resume system, or a progress API for pre-synthesis
before Phases 1–3 have shipped and been measured. Phase 1 alone may resolve the complaint entirely, and
it is ~10× smaller.

---

## 7 · Does the repo already have eviction? — **No. Nothing.**

Searched with:

```bash
grep -rni "evict\|ttl\|vacuum\|max_size\|MAX_CACHE\|cleanup\|prune\|size_limit\|quota\|retention" \
  backend/ frontend/src/ scripts/ --include=*.py --include=*.ts --include=*.svelte --include=*.sh
```

**VERIFIED — the only in-repo hit that concerns stored data is `DELETE /documents/text/cleanup`
(`backend/routers/documents.py:172-190`)**, which deletes `Book` + `Sentence` rows for *ephemeral text
snippets* older than 24 hours. Every other match is inside `backend/venv/` (pip's own code).

**There is no cache eviction, no TTL, no size cap, no LRU, and no `VACUUM` anywhere in the application.**
`AudioCache` rows are written (`tts_engine.py:119-122`) and are **never deleted** — there is no
`DELETE FROM audiocache` in the codebase. The `db/database.py` startup routine only does additive
`ALTER TABLE ... ADD COLUMN` migrations (`:29-34`).

### What that means for the 839,905,280-byte DB

| Pressure | Number |
|---|---|
| Rows added by one full-book pass (biography of the strategy) | **+5,890** |
| Bytes added by one full-book pass at 1x | **+2.0 GB (B) / +1.05 GB (A)** |
| Growth on the single heaviest recorded day (2026-05-13) | **+2,784 rows / +456 MB in one day** |
| A single 50-sentence prefetch at a new speed (what one slider drag costs) | +50 rows, **+8.9 MB**, unconditional |
| 11 speed values × 1 book, 1 voice | +64,790 rows, **+11.5–22.0 GB** |
| 7 speeds × 28 voices × 7 books | +1.15M rows, **+1.30–2.48 TiB** |

Note the sqlite file is **31.4 MB larger than the audio it holds** (839,905,280 vs 808,521,600), with
`freelist_count = 0` — i.e. overhead is page-level, not fragmentation, **today**. That changes the moment
rows start being deleted: SQLite does not return freed pages to the OS without `VACUUM`, so any future
eviction must be paired with periodic `VACUUM` (or `PRAGMA auto_vacuum`) or the file will stay large
while the data shrinks. The only index is the implicit PK autoindex on `text_hash`; there is **no index on
`created_at`**, which is precisely the column an LRU/TTL sweep would need — an eviction job should add
one, or sweep by `text_hash` batches.

**Assessment:** an 839 MB DB growing by ~50 rows (~9 MB) per speed change is currently *tolerable but
already large*, and it is **purely additive with no upper bound** — it will grow until the disk fills.
Doubling down with whole-book pre-synthesis converts a slow leak into a flood. A size cap with LRU
eviction is the single cheapest durability win available and is a **prerequisite** for any strategy that
writes more than the user actually listens to.

### Every product surveyed *does* have an expiry — this repo is the outlier

| Product | Eviction policy |
|---|---|
| **NaturalReader** | MP3s **deleted after 30 days** |
| **ElevenReader** | Offline audio **expires after 60 days**, with a notification 7 days before |
| **Libratory** | Intermediate chunk WAVs **deleted once the sync map is written**; `cleanup:chunks` sweeps leftovers |
| **OpenWebTTS** | Content-addressed cache with explicit `/api/clear_cache` and `/api/cache_size` endpoints |
| **calibre** | Keeps **no audio cache at all** — only a model cache; pre-compiled audio lives in the exported EPUB, owned by the user |
| **ebook2audiobook** | `tmp_expire = 60` days — but the sweep runs **only in the CLI path**, so a successful GUI conversion leaves intermediates on disk indefinitely |
| **This repo** | **Nothing.** No TTL, no cap, no LRU, no `VACUUM` |

The one local tool that *intends* to evict but doesn't in practice (ebook2audiobook) is the cautionary
tale: an eviction policy that is not wired into every code path is not an eviction policy. Whatever this
repo adds should run from a single place — on startup and on a timer — not from the write path of one
particular flow.

---

## 8 · Open questions / unverified

1. **No GPU measurement was possible, and the GPU estimate has a 20-fold error bar.**
   `/dev/nvidia*` is absent in this sandbox. The T600 estimate (11–20× realtime) is a **scaling argument
   from a single T4 gist**; the only direct 4 GB-VRAM datapoint found points the other way (~1× realtime,
   but for the larger autoregressive XTTSv2, not Kokoro). **No Kokoro benchmark exists for a T600,
   GTX 1650, or any MX-series card** — verified absent, not merely unfound. The plausible band is
   **~1× to ~20× realtime**, which swings single-pass GPU pre-synthesis between ~45 minutes and ~12
   hours. **This is resolvable in one command on the host:** run `docs/research/bench_synth.py` outside
   the sandbox. The recommendation does not depend on the answer — the disk arithmetic rules out
   strategy B either way — but the "GPU makes it feasible" claim does.
2. **Whether the T600 can hold the CUDA context in 4 GB natively is unmeasured.** The 2.37 GB context
   floor was measured on **Windows+WSL2**, which carries ~1.3 GiB of invisible reserve; native Linux is
   likely cheaper but nobody has measured it. A five-minute experiment
   (`torch.cuda.memory_reserved()` after `KPipeline` construction) resolves it. **Pair it with
   Sysmem Fallback → Prefer No Sysmem Fallback**, because otherwise an overflow silently degrades to
   10–100× slower instead of failing loudly.
3. **The cache's speed distribution is unrecoverable.** `AudioCache` stores `voice` but not `speed`, so
   the measured 3.70 s average cannot be decomposed. My inference that the existing rows were synthesized
   at ~1.92× average is **unverified** — adding a `speed` column would make this measurable and is worth
   doing on its own merits.
4. **Cited GPU/VRAM numbers are mostly single-user GitHub issue comments** — one machine, no
   methodology. Where sources conflicted (T600 bandwidth 160 vs 192 GB/s; `q8f16` 86 vs 160 MB) the
   conflict is flagged rather than averaged. The two SEO content sites whose Kokoro RTF tables circulate
   widely trace back to a source that self-describes its own table as "estimates" — **do not cite them.**
5. **Batch inference has no published speedup, and cannot deliver N×.** `KPipeline` has no `batch_size`;
   `KModel.forward` hardcodes `torch.LongTensor([[0, *input_ids, 0]])` (**VERIFIED in
   `venv/lib/python3.12/site-packages/kokoro/model.py:131`**), and `forward_with_tokens` looks
   batch-capable but `.squeeze()`s `pred_dur` and builds `pred_aln_trg` with `.unsqueeze(0)`, so a naive
   batch would **silently misalign rather than error**. Issue #55 ("How to do batching?") is still open.
   Forked implementations exist ([NimbleEdge/kokoro](https://github.com/NimbleEdge/kokoro),
   [wwang1110/kokoro_batch](https://github.com/wwang1110/kokoro_batch)) but publish **no batch-N
   multiplier**, and the **iSTFT/vocoder being ~71% of forward-pass time** bounds the upside.
   **Treat GPU batching as unquantified research, not a plan.**
6. **Neither TensorRT nor `torch.compile` has a citable win here.** Zero published TensorRT benchmarks
   for Kokoro (two conversion attempts fail on dynamic shapes); `torch.compile` has one 1.34× point and
   is known to fail outright in both modes ([pytorch#149570](https://github.com/pytorch/pytorch/issues/149570)).
   Do not plan around either.
7. **`torch.get_num_threads()` is 8 on a 16-thread CPU.** Untested whether raising it improves CPU RTF;
   a cheap experiment with potentially meaningful upside for the CPU path, which is the path most users
   will actually be on.
8. **The `bench_blocking.py` absolute per-sentence times are not comparable to `bench_synth.py`'s** —
   different sentence samples (front-matter-heavy vs whole-book stride). The *ratios* (G2P 0.04% of
   cost; `overlap_ratio` 1.000) are the durable findings and are what the recommendation rests on.
9. **Premise correction:** the brief's "6–24 s/sentence in the repo's own docs/issues" is
   **not present in this checkout** (§1.3). My measurement coincidentally brackets it.
10. **Premise correction:** the reading UI offers **7** speeds, not 11
    (`MediaBar.svelte:24`); 11 is computed here only because the brief asked for it.
11. **Premise correction:** the brief describes the T600 as having tensor cores to exploit for fp16. It
    does not — TU117 has none (§5.3). This removes an entire optimization avenue from consideration.
12. **Whether Speechify's iOS "download for offline" stores audio bytes or just the document is
    unverified** — its own help article is self-contradictory, and its audio-export path is documented as
    web-only. It does not affect any conclusion here.

---

## Appendix · Reproducing everything

All scripts are in `docs/research/`, read the DB read-only, and write only into `docs/research/`:

```bash
cd /home/christia50/Repos/ebook-reader

# 1. Per-sentence synthesis cost on this host (CPU). ~5 min. Writes bench_synth.json
cd backend && venv/bin/python ../docs/research/bench_synth.py

# 2. Event-loop blocking + G2P-vs-torch split + concurrency overlap. Writes bench_blocking.json
cd backend && venv/bin/python ../docs/research/bench_blocking.py

# 3. All arithmetic in §2. Reads the DB + the bench JSON. Writes arithmetic.json
cd backend && venv/bin/python ../docs/research/calc_arithmetic.py
```

> Per the updated `CLAUDE.md`, new work should run through `uv` (`cd backend && uv run python ...`)
> against `backend/.venv`. These benchmarks were run against `backend/venv`, which also exists and
> resolves to the same Python 3.12 / torch 2.14.0 / kokoro 0.9.4 stack; identical results are expected.
>
> **Also in this directory, but not produced by this research:** `01-speed-strategy-bench.py` and
> `01-speed-strategy-durationmodel.py` (a sibling investigation into the speed dimension), and
> `kokoro-82m-t600-4gb-research.md`, `kokoro-accelerated-backends-benchmarks.md`,
> `cuda-display-gpu-low-vram-findings.md` (delegated deep research on Kokoro on the T600). The GPU and
> quantization claims cited here are drawn from those, and they should be read directly for their own
> evidence labels.

**Raw artifacts:** `bench_synth.json`, `bench_blocking.json`, `arithmetic.json`, `bench_synth.log`,
`bench_blocking.log`.
