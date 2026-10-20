"""Time Kokoro synthesis on a GPU (or CPU) with 1..N parallel processes, on real book sentences.

    # AMD (ROCm env, never `uv run`):
    HSA_OVERRIDE_GFX_VERSION=10.3.0 .venv-rocm/bin/python scripts/bench_gpu_synth.py \
        --book-prefix 6f2c --procs 1,2,4 --sentences 80 [--device cuda|cpu] [--out ../docs/research/gpu-synth-bench.json]

Each process loads the engine, warms up, waits for a shared start time, then synthesizes its own
slice of the book (different sentences per process). Reported speed is total audio / wall time
across all processes, so process start-up and model load are excluded. Writes nothing to the app
database (read-only query). ``--device cuda`` is torch's name for ROCm as well.

Results are only comparable on an otherwise idle machine: run it while a render is encoding and
the CPU-side tokenizer/phonemizer competes with it (the report prints the 1-minute load average).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SR = 24000


def load_sentences(db_path: Path, prefix: str | None, count: int, offset: int) -> list[str]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    where, args = ("book_id like ?", (prefix + "%",)) if prefix else ("1=1", ())
    rows = db.execute(f"select text from sentence where {where} and filtered = 0 "
                      f"order by book_id, [index] limit 5000 offset ?", (*args, offset)).fetchall()
    out = [r[0] for r in rows if 30 <= len(r[0]) <= 400]
    return out[:count]


def worker(args) -> None:
    sys.path.insert(0, str(BACKEND))
    import torch
    from services import engine_manager

    texts = load_sentences(Path(args.db), args.book_prefix, args.sentences,
                           args.offset + args.slice * 400)
    if len(texts) < args.sentences:
        raise SystemExit(f"only {len(texts)} usable sentences at slice {args.slice}")
    pipe, err = engine_manager.build_local(args.device)
    if pipe is None:
        raise SystemExit(f"engine unavailable: {err}")

    def synth(text: str) -> float:
        n = 0
        for _g, _p, audio in pipe(text, voice=args.voice, speed=1.0):
            n += len(audio)
        if args.device != "cpu":
            torch.cuda.synchronize()
        return n / SR

    for t in texts[:3]:
        synth(t)
    while time.time() < args.start:
        time.sleep(0.01)
    started, lat, audio = time.time(), [], 0.0
    for t in texts:
        s = time.perf_counter()
        audio += synth(t)
        lat.append(time.perf_counter() - s)
    print("RESULT " + json.dumps({"audio_s": audio, "start": started, "end": time.time(),
                                  "lat": lat, "miopen": bool(torch.backends.cudnn.enabled)}))


def run(args) -> dict:
    results = {}
    for n in [int(x) for x in args.procs.split(",")]:
        start = time.time() + args.warm_s
        procs = []
        for i in range(n):
            cmd = [sys.executable, __file__, "--worker", "--slice", str(i), "--start", str(start),
                   "--sentences", str(args.sentences), "--device", args.device, "--voice", args.voice,
                   "--offset", str(args.offset), "--db", args.db]
            if args.book_prefix:
                cmd += ["--book-prefix", args.book_prefix]
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outs = [p.communicate() for p in procs]
        rs = []
        for (out, err), p in zip(outs, procs):
            line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
            if p.returncode != 0 or line is None:
                tail = (err.strip().splitlines() or ["no stderr"])[-1]
                print(f"  procs={n}: a worker failed (exit {p.returncode}): {tail}", file=sys.stderr)
                continue
            rs.append(json.loads(line[7:]))
        if len(rs) != n:
            results[n] = {"error": f"{n - len(rs)} of {n} workers failed"}
            continue
        audio = sum(r["audio_s"] for r in rs)
        wall = max(r["end"] for r in rs) - min(r["start"] for r in rs)
        lat = sorted(x for r in rs for x in r["lat"])
        results[n] = {"audio_s": round(audio, 1), "wall_s": round(wall, 1),
                      "speed_x": round(audio / wall, 1),
                      "lat_median_s": round(statistics.median(lat), 3),
                      "lat_p95_s": round(lat[int(0.95 * (len(lat) - 1))], 3),
                      "miopen_enabled": rs[0]["miopen"]}
        print(f"procs={n}: {results[n]}")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--procs", default="1,2,4")
    ap.add_argument("--sentences", type=int, default=80, help="per process")
    ap.add_argument("--device", default="cuda", help="cuda (also ROCm) or cpu")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--book-prefix", default=None, help="book id prefix; default any book")
    ap.add_argument("--offset", type=int, default=2000, help="skip front matter")
    ap.add_argument("--db", default=str(BACKEND / "ebook_reader.db"))
    ap.add_argument("--warm-s", type=float, default=45, help="seconds allowed for model load")
    ap.add_argument("--out", default=None)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--slice", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--start", type=float, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        return worker(args)
    results = run(args)
    report = {"device": args.device, "voice": args.voice, "sentences_per_proc": args.sentences,
              "load_1m": os.getloadavg()[0], "date": time.strftime("%F %T"), "results": results}
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
