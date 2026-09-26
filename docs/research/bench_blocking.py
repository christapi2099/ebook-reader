"""Measure WHY the app lags: is Kokoro's synchronous inference blocking the asyncio
event loop, and how much of the cost is G2P (pure Python, holds the GIL) vs the
torch model forward (releases the GIL)?

Read-only: does not touch backend/ebook_reader.db.
Run: cd backend && venv/bin/python ../docs/research/bench_blocking.py
"""
import asyncio
import json
import os
import sqlite3
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SAMPLE = [
    "This is a short sentence.",
    "Refactoring is the process of changing the internal structure of software to make it easier to understand and cheaper to modify without changing its observable behavior.",
    "The second rule of simple design is that a design should have as few moving parts as possible, and the third is that it should not contain duplication.",
]


def real_sentences(n: int = 3):
    db = REPO / "backend" / "ebook_reader.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT s.text FROM sentence s JOIN book b ON b.id = s.book_id
           WHERE b.title = 'cleancodebook.pdf' AND s.filtered = 0
           ORDER BY s."index" LIMIT 400"""
    ).fetchall()
    con.close()
    stride = max(1, len(rows) // n)
    return [r[0] for r in rows[::stride][:n]]


async def main():
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = {"device": device, "cuda_available": torch.cuda.is_available()}

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from kokoro import KPipeline
    t0 = time.perf_counter()
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=device)
    out["model_load_s"] = round(time.perf_counter() - t0, 3)

    texts = real_sentences(3)

    # ---- 1. Split: G2P (misaki, pure python) vs torch forward -----------------
    from misaki import en as misaki_en
    g2p = misaki_en.G2P()
    g2p_times, model_times = [], []
    for text in texts:
        t0 = time.perf_counter()
        _, tokens = g2p(text)
        g2p_times.append(time.perf_counter() - t0)

        ps = "".join(t.phonemes for t in tokens if t.phonemes)
        pack = pipe.load_voice("af_heart").to(pipe.model.device)
        t0 = time.perf_counter()
        with torch.no_grad():
            pipe.model(ps, pack[len(ps) - 1], 1.0, return_output=True)
        model_times.append(time.perf_counter() - t0)

    out["g2p"] = {
        "times_s": [round(t, 4) for t in g2p_times],
        "mean_s": round(statistics.mean(g2p_times), 4),
    }
    out["torch_forward"] = {
        "times_s": [round(t, 4) for t in model_times],
        "mean_s": round(statistics.mean(model_times), 4),
    }

    # ---- 2. Event-loop stall while synthesizing (mimics TTSEngine.stream_job) --
    async def heartbeat(interval: float, stop: asyncio.Event, gaps: list):
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(interval)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    def synth_blocking(text, voice="af_heart", speed=1.0):
        """Exactly what TTSEngine._call_kokoro + the `for result in results` loop does."""
        gen = pipe(text, voice=voice, speed=speed)
        parts = []
        for result in gen:
            a = result[-1]
            parts.append(len(a.flatten()) if hasattr(a, "flatten") else len(a))
        return parts

    gaps: list[float] = []
    stop = asyncio.Event()
    hb = asyncio.create_task(heartbeat(0.01, stop, gaps))

    async def synth_task(text):
        t0 = time.perf_counter()
        # synchronous, blocking, no thread offload — same as production code
        synth_blocking(text)
        return time.perf_counter() - t0

    await asyncio.sleep(0.05)  # let heartbeat establish a baseline
    baseline = statistics.median(gaps) if gaps else None
    t0 = time.perf_counter()
    durs = []
    for text in texts:
        durs.append(await synth_task(text))
    wall = time.perf_counter() - t0
    stop.set()
    await hb

    out["event_loop"] = {
        "heartbeat_interval_s": 0.01,
        "baseline_median_gap_s": round(baseline, 5) if baseline else None,
        "max_gap_s": round(max(gaps), 4),
        "n_gaps": len(gaps),
        "gaps_over_1s": sum(1 for g in gaps if g > 1.0),
        "gaps_over_0_1s": sum(1 for g in gaps if g > 0.1),
        "synth_wall_s": round(wall, 3),
        "per_sentence_s": [round(d, 3) for d in durs],
        "interpretation": (
            "max_gap is how long the asyncio event loop was unable to run ANY other "
            "task (HTTP request, WebSocket frame, cancellation check) while one "
            "sentence was being synthesized."
        ),
    }

    # ---- 3. Two concurrent "sessions" (play + prefetch) — do they overlap? -----
    gaps2: list[float] = []
    stop2 = asyncio.Event()
    hb2 = asyncio.create_task(heartbeat(0.01, stop2, gaps2))
    await asyncio.sleep(0.05)

    async def worker(name, text):
        t0 = time.perf_counter()
        synth_blocking(text)
        return name, round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    got = await asyncio.gather(worker("play", texts[0]), worker("prefetch", texts[1]))
    both_wall = time.perf_counter() - t0
    stop2.set()
    await hb2
    out["concurrency"] = {
        "individual_s": {n: d for n, d in got},
        "sum_of_individuals_s": round(sum(d for _, d in got), 3),
        "wall_when_concurrent_s": round(both_wall, 3),
        "overlap_ratio": round(sum(d for _, d in got) / both_wall, 3),
        "max_event_loop_gap_s": round(max(gaps2), 4),
        "interpretation": (
            "overlap_ratio ~= 1.0 means the two tasks were fully SERIALIZED (no "
            "parallelism): prefetch steals wall-clock from playback one-for-one."
        ),
    }

    (HERE / "bench_blocking.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
