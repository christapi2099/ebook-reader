"""Time a real render of N sentences of a book through the full render pipeline.

    # AMD (ROCm env, never `uv run`); run it once per worker count:
    for w in 1 4; do RENDER_SYNTH_WORKERS=$w HSA_OVERRIDE_GFX_VERSION=10.3.0 \
        .venv-rocm/bin/python scripts/render_timed.py --book-prefix 6f2c --sentences 300; done

Reads sentences from the app DB READ-ONLY and renders them in a throwaway DB and renders
directory (the same sandbox as ``render_baseline.py``); the app's data is never written. The
engine is the real torch Kokoro on the GPU (``--device cuda``), real CTC aligner, real ffmpeg
(one speed tier to keep encode short). Reports wall time, speed_x (audio seconds / wall) for the
whole render and for the synthesize phase, peak VRAM used on the card (sysfs, whole device, minus
what was in use before) and peak RAM (PSS of this process plus all its children).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import render_baseline as rb  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent


def load_sentences(db: Path, prefix: str, count: int, offset: int) -> list[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select text from sentence where book_id like ? and filtered = 0 "
                       "order by book_id, [index] limit 5000 offset ?", (prefix + "%", offset)).fetchall()
    return [r[0] for r in rows if 30 <= len(r[0]) <= 400][:count]


def vram_used() -> int | None:
    for p in sorted(Path("/sys/class/drm").glob("card*/device/mem_info_vram_used")):
        try:
            return int(p.read_text())
        except OSError:
            pass
    return None


def tree_rss(pid: int) -> int:
    """PSS bytes (shared pages split fairly, so the torch libraries are not counted once per
    process) of ``pid`` and all of its descendants, from /proc/*/smaps_rollup."""
    kids: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            kids.setdefault(ppid, []).append(int(d.name))
    total, stack = 0, [pid]
    while stack:
        p = stack.pop()
        try:
            for line in Path(f"/proc/{p}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    total += int(line.split()[1]) * 1024
        except OSError:
            pass
        stack.extend(kids.get(p, []))
    return total


class Peaks(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.base_vram = vram_used() or 0
        self.vram = self.ram = 0
        self._stop_ev = threading.Event()

    def run(self):
        while not self._stop_ev.wait(0.25):
            v = vram_used()
            if v is not None:
                self.vram = max(self.vram, v - self.base_vram)
            self.ram = max(self.ram, tree_rss(os.getpid()))

    def stop(self):
        self._stop_ev.set()
        self.join()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book-prefix", required=True)
    ap.add_argument("--sentences", type=int, default=300)
    ap.add_argument("--offset", type=int, default=2000)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--db", default=str(BACKEND / "ebook_reader.db"))
    args = ap.parse_args()

    corpus = load_sentences(Path(args.db), args.book_prefix, args.sentences, args.offset)
    if len(corpus) < args.sentences:
        raise SystemExit(f"only {len(corpus)} usable sentences")
    from services import render_jobs as rj, render_stats
    from services import render_worker as rw

    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    engine = rb.build_engine("kokoro-gpu" if args.device != "cpu" else "kokoro-cpu")
    for t in corpus[:3]:  # warm the in-process pipeline so serial is not penalised for it
        engine.synthesize_for_render(t, "af_heart", 1.0)
    voice = "af_heart"
    with tempfile.TemporaryDirectory(prefix="render-timed-") as tmp:
        rb.make_sandbox(Path(tmp), corpus, voice)
        synth = rw.engine_synthesizer(engine)
        workers = rw.resolve_synth_workers(synth)
        now = datetime.now(timezone.utc).replace(year=2030)
        assert rj.claim_next("timed", now, lease_s=3600, debounce_s=0) == rb.BOOK_ID
        align = {"s": 0.0}
        orig_align = rw.align_sentence

        def timed_align(*a, **kw):  # summed over threads, so it can exceed wall time
            t = time.perf_counter()
            try:
                return orig_align(*a, **kw)
            finally:
                align["s"] += time.perf_counter() - t

        rw.align_sentence = timed_align
        peaks = Peaks()
        peaks.start()
        t0 = time.perf_counter()
        outcome = rw.render_book(rb.BOOK_ID, "timed", synthesizer=synth, tiers=(1.5,),
                                 now=lambda: now, lease_s=3600, raise_errors=True)
        wall = time.perf_counter() - t0
        peaks.stop()
        gens = sorted(p for p in (rb.storage.RENDERS_DIR / rb.BOOK_ID).iterdir() if p.name.isdigit())
        manifest = json.loads((gens[-1] / "manifest.json").read_text())
        audio_s = manifest["duration_ms"] / 1000.0 if "duration_ms" in manifest else None
        phases = render_stats.read(rb.BOOK_ID).get("phase_timings", {})
    synth_s = phases.get("synthesize")
    out = {"outcome": outcome, "workers_requested": workers, "sentences": len(corpus),
           "audio_s": round(audio_s, 1) if audio_s else None, "wall_s": round(wall, 1),
           "speed_x_whole_render": round(audio_s / wall, 1) if audio_s else None,
           "synthesize_phase_s": synth_s,
           "speed_x_synthesize_phase": round(audio_s / synth_s, 1) if audio_s and synth_s else None,
           "peak_vram_gb_above_baseline": round(peaks.vram / 2**30, 2),
           "peak_ram_gb_process_tree": round(peaks.ram / 2**30, 2),
           "align_thread_seconds_total": round(align["s"], 1), "phase_timings": phases, "date": time.strftime("%F %T")}
    print("TIMED " + json.dumps(out))


if __name__ == "__main__":
    main()
