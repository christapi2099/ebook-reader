#!/usr/bin/env python3
"""Add WebM (Opus) copies to an already published render.

    cd backend && uv run python scripts/convert_to_webm.py <book_id> [--gen N] [--dry-run]

Why: Firefox on Linux mishandles an Ogg Opus file longer than 12 h 25 m (see
services/webm_remux.py). This writes ``audio_{tier}.webm`` NEXT TO each existing
``audio_{tier}.opus`` of the book's ready generation (or ``--gen``) with a stream copy:
no re-encode, no re-render, the Ogg files, manifest and database are not touched. The
reader switches to WebM by itself once every tier has a copy (the manifest response
lists ``audio_formats``). Each copy is written to ``.part`` and renamed only after its
duration matches the Ogg file's; a failure removes the copies this run made.
Safe to re-run: existing copies are rebuilt.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402
from services import webm_remux  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("book_id")
    ap.add_argument("--gen", type=int, help="generation (default: the highest present)")
    ap.add_argument("--workers", type=int, default=None, help="parallel remuxes (default RENDER_WEBM_WORKERS or 4)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be written")
    a = ap.parse_args()

    root = Path(storage.RENDERS_DIR) / a.book_id
    if a.gen is None:
        gens = sorted(int(p.name) for p in root.glob("[0-9]*") if p.is_dir() and p.name.isdigit())
        if not gens:
            print(f"no generations under {root}", file=sys.stderr)
            return 2
        a.gen = gens[-1]
    gen_dir = root / str(a.gen)
    files = sorted(gen_dir.glob("audio_*.opus"))
    if not files:
        print(f"no audio_*.opus in {gen_dir}", file=sys.stderr)
        return 2
    total = sum(f.stat().st_size for f in files)
    print(f"{gen_dir}: {len(files)} tiers, {total / 1e6:.0f} MB of Ogg; formats now {webm_remux.available_formats(gen_dir)}")
    if a.dry_run:
        for f in files:
            print("  would write", webm_remux.webm_path(f).name)
        return 0
    t0 = time.time()
    try:
        webm_remux.remux_tiers(files, a.workers)
    except webm_remux.RemuxError as exc:
        print(f"FAILED, no WebM copies kept: {exc}", file=sys.stderr)
        return 1
    out = [webm_remux.webm_path(f) for f in files]
    print(f"wrote {len(out)} WebM files, {sum(f.stat().st_size for f in out) / 1e6:.0f} MB, in {time.time() - t0:.0f} s")
    print(f"formats now {webm_remux.available_formats(gen_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
