"""Read-only benchmark: Kokoro-82M per-sentence synthesis cost on this host.

Writes NOTHING to backend/ebook_reader.db (opens it with mode=ro).
Output: docs/research/bench_synth.json + stdout log.

Run:  cd backend && venv/bin/python ../docs/research/bench_synth.py
"""
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def load_sentences(n: int = 30, book_like: str = "cleancodebook.pdf"):
    """Pull real sentence texts, read-only, from the largest real book in the DB."""
    db = REPO / "backend" / "ebook_reader.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    (title,) = cur.execute(
        "SELECT title FROM book WHERE title = ?", (book_like,)
    ).fetchone()
    sql = """
        SELECT s.text, LENGTH(s.text)
        FROM sentence s JOIN book b ON b.id = s.book_id
        WHERE b.title = ? AND s.filtered = 0
        ORDER BY s."index"
    """
    rows = cur.execute(sql, (book_like,)).fetchall()
    con.close()
    # Deterministic stride sample across the whole book (chapter-diverse, not just front matter)
    stride = max(1, len(rows) // n)
    sample = rows[::stride][:n]
    return title, sample, len(rows)


def main():
    t_start = time.perf_counter()
    import numpy as np
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    result = {
        "host": {
            "torch": torch.__version__,
            "device_requested": device,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_count": torch.cuda.device_count(),
            "torch_num_threads": torch.get_num_threads(),
            "torch_num_interop_threads": torch.get_num_interop_threads(),
            "cpu_count_logical": os.cpu_count(),
        },
        "note": "GPU measurements UNAVAILABLE in this sandbox (/dev/nvidia* not exposed).",
    }
    try:
        import psutil
        result["host"]["cpu_count_physical"] = psutil.cpu_count(logical=False)
    except Exception:
        pass

    print("[bench] loading KPipeline ...", flush=True)
    t0 = time.perf_counter()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=device)
    result["model_load_s"] = round(time.perf_counter() - t0, 3)
    print(f"[bench] load took {result['model_load_s']}s", flush=True)

    title, sample, total_speakable = load_sentences(30)
    result["corpus"] = {
        "book": title,
        "speakable_sentences_in_book": total_speakable,
        "sampled": len(sample),
        "mean_chars": round(statistics.mean(len(t) for t, _ in sample), 1),
    }
    print(f"[bench] book={title} speakable={total_speakable} sampled={len(sample)}", flush=True)

    VOICE = "af_heart"
    timings = []
    # warmup: first call pays lazy-init costs (G2P, voice pack, cudnn/cpu kernels)
    for i, (text, nchars) in enumerate(sample):
        audio_len = 0
        t0 = time.perf_counter()
        gen = pipe(text, voice=VOICE, speed=1.0)
        for r in gen:
            a = r[-1]
            a = a if isinstance(a, np.ndarray) else np.array(a)
            audio_len += len(a.flatten())
        dt = time.perf_counter() - t0
        audio_s = audio_len / 24000.0
        timings.append({
            "i": i,
            "chars": nchars,
            "wall_s": round(dt, 4),
            "audio_s": round(audio_s, 3),
            "rtf": round(dt / audio_s, 4) if audio_s > 0 else None,
            "warm": i > 0,
        })
        print(
            f"[bench] {i:2d} chars={nchars:4d} wall={dt:7.3f}s audio={audio_s:6.3f}s "
            f"rtf={dt/audio_s if audio_s else 0:6.3f}",
            flush=True,
        )

    warm = [t for t in timings if t["warm"] and t["rtf"]]
    result["timings"] = timings
    if warm:
        result["summary"] = {
            "cold_first_call_wall_s": timings[0]["wall_s"],
            "warm_n": len(warm),
            "warm_mean_wall_s": round(statistics.mean(t["wall_s"] for t in warm), 4),
            "warm_median_wall_s": round(statistics.median(t["wall_s"] for t in warm), 4),
            "warm_stdev_wall_s": round(statistics.stdev(t["wall_s"] for t in warm), 4) if len(warm) > 1 else None,
            "warm_min_wall_s": round(min(t["wall_s"] for t in warm), 4),
            "warm_max_wall_s": round(max(t["wall_s"] for t in warm), 4),
            "warm_mean_audio_s": round(statistics.mean(t["audio_s"] for t in warm), 4),
            "warm_mean_rtf": round(statistics.mean(t["rtf"] for t in warm), 4),
            "warm_median_rtf": round(statistics.median(t["rtf"] for t in warm), 4),
            "warm_max_rtf": round(max(t["rtf"] for t in warm), 4),
            "sentence_per_second": round(1.0 / statistics.mean(t["wall_s"] for t in warm), 4),
        }
    result["total_wall_s"] = round(time.perf_counter() - t_start, 2)

    out = HERE / "bench_synth.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\n[bench] wrote {out}")
    print(json.dumps(result.get("summary", {}), indent=2))


if __name__ == "__main__":
    sys.exit(main())
