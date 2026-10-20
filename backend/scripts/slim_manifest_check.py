"""Sizes, parse times and an exhaustive equality check of a render's slim manifest.

Reads a published manifest.json (never writes it), builds the slim form
(``services/render_manifest.py``) and checks that, for every tier, segment and
word, ``tier_segments(slim, tier)`` equals the schema 1 file's own values (start,
end, index, gap, box, timing source). With ``--url`` it also fetches what the
running backend serves (``GET /library/{id}/render``, gzip) and checks it is the
same slim manifest. Optional ``--node`` times ``JSON.parse`` of both forms in node.

Usage (from backend/):
    uv run python scripts/slim_manifest_check.py renders/<book>/<gen>/manifest.json
    uv run python scripts/slim_manifest_check.py --book <book> [--gen N] [--url http://127.0.0.1:8000] [--node]

Exit 0 = slim equals the source on every tier/segment/word (and the served copy),
1 = a mismatch, 2 = could not run. Peak RAM ~1.5 GB for a 358 MB schema 1 file
(~2.5 GB with --url, which holds both copies).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402
from services import render_manifest as rm  # noqa: E402

TOL_MS = rm.TIME_TOL_MS


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def compare(source: dict, slim: dict) -> dict:
    """Every tier/segment/word of ``source`` (either schema) against ``slim``."""
    view = rm.expand_v1(source)
    stats = {"tiers": 0, "segments": 0, "words": 0, "mismatches": [], "max_abs_err_ms": 0.0,
             "embedded_tiers": sorted(k for k, t in slim.get("tiers", {}).items()
                                      if isinstance(t, dict) and "segments" in t)}

    def bad(msg):
        stats["mismatch_count"] = stats.get("mismatch_count", 0) + 1
        if len(stats["mismatches"]) < 20:
            stats["mismatches"].append(msg)

    def num(a, b, where):
        err = abs(a - b)
        stats["max_abs_err_ms"] = max(stats["max_abs_err_ms"], err)
        if not math.isclose(a, b, rel_tol=0.0, abs_tol=TOL_MS):
            bad(f"{where}: {a!r} != {b!r}")

    for key, tier in (view.get("tiers") or {}).items():
        stats["tiers"] += 1
        st = slim["tiers"].get(key)
        if st is None:
            bad(f"tier {key} missing from slim")
            continue
        for fld in ("requested_factor", "actual_factor", "max_drift_ms"):
            if tier.get(fld) != st.get(fld):
                bad(f"tier {key}.{fld}: {tier.get(fld)!r} != {st.get(fld)!r}")
        want = tier.get("segments") or []
        got = rm.tier_segments(slim, key)
        if len(want) != len(got):
            bad(f"tier {key}: {len(want)} segments != {len(got)}")
            continue
        for s0, s1 in zip(want, got):
            stats["segments"] += 1
            w = f"tier {key} seg {s0.get('index')}"
            if s0.get("index") != s1.get("index") or s0.get("gap_ms") != s1.get("gap_ms"):
                bad(f"{w}: index/gap differ")
            num(s0["start_ms"], s1["start_ms"], f"{w} start")
            num(s0["end_ms"], s1["end_ms"], f"{w} end")
            ws0, ws1 = s0.get("words") or [], s1.get("words") or []
            if len(ws0) != len(ws1):
                bad(f"{w}: {len(ws0)} words != {len(ws1)}")
                continue
            for i, (a, b) in enumerate(zip(ws0, ws1)):
                stats["words"] += 1
                if a.get("box") != b.get("box") or a.get("timing_source") != b.get("timing_source"):
                    bad(f"{w} word {i}: box/timing_source differ")
                num(a["start_ms"], b["start_ms"], f"{w} word {i} start")
                num(a["end_ms"], b["end_ms"], f"{w} word {i} end")
    stats["ok"] = not stats["mismatches"]
    return stats


def fetch(url: str, book: str) -> tuple[bytes, int, float, dict]:
    req = urllib.request.Request(f"{url.rstrip('/')}/library/{book}/render",
                                 headers={"Accept-Encoding": "gzip"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        wire = r.read()
        headers = dict(r.headers)
    elapsed = time.perf_counter() - t
    enc = {k.lower(): v for k, v in headers.items()}.get("content-encoding", "")
    body = gzip.decompress(wire) if enc == "gzip" else wire
    return body, len(wire), elapsed, headers


NODE_BENCH = r"""
const fs = require('fs');
for (const f of process.argv.slice(1)) {
  const text = fs.readFileSync(f, 'utf8');
  const times = [];
  for (let i = 0; i < 3; i++) { const t = performance.now(); JSON.parse(text); times.push(performance.now() - t); }
  console.log(JSON.stringify({file: f, ms: times.map(Math.round)}));
}
"""


def node_parse(paths: list[Path]) -> list[str]:
    node = shutil.which("node")
    if not node:
        return ["node not found; skipped"]
    r = subprocess.run([node, "--max-old-space-size=4096", "-e", NODE_BENCH, *map(str, paths)],
                       capture_output=True, text=True, timeout=600)
    return (r.stdout or r.stderr).strip().splitlines()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", type=Path, help="a manifest.json path")
    ap.add_argument("--book", help="book id under RENDERS_DIR")
    ap.add_argument("--gen", type=int, help="generation (default: the highest on disk)")
    ap.add_argument("--url", help="backend base URL to fetch the served manifest from")
    ap.add_argument("--node", action="store_true", help="also time JSON.parse in node")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)

    path = args.manifest
    if path is None:
        if not args.book:
            ap.error("give a manifest path or --book")
        bdir = Path(storage.RENDERS_DIR) / args.book
        gens = sorted((int(p.name) for p in bdir.iterdir() if p.name.isdigit()), reverse=True) \
            if bdir.is_dir() else []
        gen = args.gen if args.gen is not None else (gens[0] if gens else None)
        if gen is None:
            print(f"no generation under {bdir}", file=sys.stderr)
            return 2
        path = bdir / str(gen) / "manifest.json"
    if not path.is_file():
        print(f"{path}: not found", file=sys.stderr)
        return 2

    rep: dict = {"source": str(path)}
    t = time.perf_counter()
    raw = path.read_bytes()
    rep["source_bytes"] = len(raw)
    source = json.loads(raw)
    rep["source_schema"] = source.get("schema")
    rep["py_parse_source_s"] = round(time.perf_counter() - t, 2)
    del raw
    t = time.perf_counter()
    slim = rm.slim_manifest(source)
    rep["slim_build_s"] = round(time.perf_counter() - t, 2)
    body = json.dumps(slim, separators=(",", ":")).encode()
    rep["slim_bytes"] = len(body)
    t = time.perf_counter()
    rep["slim_gzip_bytes"] = len(gzip.compress(body, compresslevel=6, mtime=0))
    rep["gzip_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    json.loads(body)
    rep["py_parse_slim_s"] = round(time.perf_counter() - t, 2)
    rep["tiers"] = {k: t.get("actual_factor") for k, t in slim.get("tiers", {}).items()
                    if isinstance(t, dict)}
    t = time.perf_counter()
    rep["check"] = compare(source, slim)
    rep["check_s"] = round(time.perf_counter() - t, 2)
    ok = rep["check"]["ok"]

    if args.url:
        book = args.book or path.parent.parent.name
        served, wire, elapsed, headers = fetch(args.url, book)
        rep["served"] = {"wire_bytes": wire,
                         "decoded_bytes": len(served), "seconds": round(elapsed, 2),
                         "content_encoding": {k.lower(): v for k, v in headers.items()}
                         .get("content-encoding"),
                         "etag": {k.lower(): v for k, v in headers.items()}.get("etag")}
        served_m = json.loads(served)
        same = served_m == json.loads(body)
        rep["served"]["equals_local_slim"] = same
        ok = ok and same

    if args.node:
        with tempfile.TemporaryDirectory(prefix="slim-check-") as d:
            p = Path(d) / "slim.json"
            p.write_bytes(body)
            rep["node_parse"] = node_parse([p, path])

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        c = rep["check"]
        print(f"{path}: schema {rep['source_schema']}, {_mb(rep['source_bytes'])} "
              f"(python parse {rep['py_parse_source_s']} s)")
        print(f"slim: {_mb(rep['slim_bytes'])}, gzip {_mb(rep['slim_gzip_bytes'])} "
              f"(build {rep['slim_build_s']} s, gzip {rep['gzip_s']} s, python parse {rep['py_parse_slim_s']} s)")
        print(f"tiers: {rep['tiers']}; embedded (not 1x/actual): {c['embedded_tiers'] or 'none'}")
        print(f"checked {c['tiers']} tiers, {c['segments']} segments, {c['words']} words in "
              f"{rep['check_s']} s: max |err| {c['max_abs_err_ms']:.3g} ms, "
              f"{'OK' if c['ok'] else 'MISMATCH'}")
        for m in c["mismatches"]:
            print("  " + m)
        if "served" in rep:
            s = rep["served"]
            print(f"served: {_mb(s['wire_bytes'])} on the wire ({s['content_encoding'] or 'identity'}), "
                  f"{_mb(s['decoded_bytes'])} decoded, {s['seconds']} s, etag {s['etag']}, "
                  f"equals local slim: {s['equals_local_slim']}")
        for line in rep.get("node_parse", []):
            print(f"node JSON.parse: {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
