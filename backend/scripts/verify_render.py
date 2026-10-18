"""Run every render-manifest invariant against the files on disk.

Usage:
    cd backend && uv run python scripts/verify_render.py <book_id> [--generation N] [--json]

Exit code 0 = all invariants hold, 1 = failures, 2 = could not run (bad book).
Library use: ``verify_render(book_id, generation=None) -> VerifyReport``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

import db.database as _db  # noqa: E402
import storage  # noqa: E402
from db.models import Book, Sentence  # noqa: E402

DURATION_TOL_MS = 50.0
FACTOR_TOL = 0.005
DRIFT_MAX_MS = 60.0
GAP_DEFAULT_MS = 120
EPS_MS = 0.5


@dataclass
class VerifyReport:
    book_id: str
    generation: int | None = None
    failures: list[str] = field(default_factory=list)
    checks: int = 0
    info: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {"book_id": self.book_id, "generation": self.generation, "ok": self.ok,
                "checks": self.checks, "failures": self.failures, "info": self.info}


def _probe_ms(path: Path) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return float(r.stdout.strip()) * 1000.0
    except (OSError, ValueError):
        return None


def _quality(srcs: set) -> str:
    if "estimated" in srcs:
        return "estimated"
    if "aligned" in srcs:
        return "aligned"
    return "exact"


def verify_render(book_id: str, generation: int | None = None) -> VerifyReport:
    rep = VerifyReport(book_id)

    def check(cond: bool, msg: str) -> bool:
        rep.checks += 1
        if not cond:
            rep.failures.append(msg)
        return cond

    with Session(_db.engine) as s:
        book = s.get(Book, book_id)
        if book is None:
            check(False, f"book {book_id} not found")
            return rep
        ready_gen, ready_fp = book.ready_generation, book.ready_fingerprint
        sents = list(s.exec(select(Sentence).where(Sentence.book_id == book_id)
                            .order_by(Sentence.index)))
    gen = generation if generation is not None else ready_gen
    rep.generation = gen
    if not check(gen is not None, "book has no ready generation"):
        return rep

    book_dir = Path(storage.RENDERS_DIR) / book_id
    gdir = book_dir / str(gen)
    if not check(gdir.is_dir(), f"generation dir missing: {gdir}"):
        return rep
    if ready_gen is not None:
        check((book_dir / str(ready_gen)).is_dir(), f"ready_generation dir {ready_gen} missing")
    if book_dir.is_dir():
        for child in sorted(book_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(".tmp-") \
                    and child.name != str(ready_gen):
                check(False, f"orphan generation dir: {child.name} (ready is {ready_gen})")

    mpath = gdir / "manifest.json"
    try:
        m = json.loads(mpath.read_text())
    except (OSError, ValueError) as e:
        check(False, f"manifest.json unreadable: {e}")
        return rep
    check(m.get("schema") == 1, f"manifest schema {m.get('schema')!r} != 1")
    check(m.get("base_effective_speed") == 1.0,
          f"base_effective_speed {m.get('base_effective_speed')!r} != 1.0")
    check((gdir / "base.flac").is_file(), "base.flac missing")
    if gen == ready_gen:
        check(m.get("fingerprint") == ready_fp,
              f"fingerprint {m.get('fingerprint')!r} != Book.ready_fingerprint {ready_fp!r}")

    live = [x for x in sents if not x.filtered]
    by_idx = {x.index: x for x in live}
    rep.info["sentences"] = len(live)
    tiers = m.get("tiers") or {}
    check(bool(tiers), "manifest has no tiers")
    check("1.0" in tiers, "tier 1.0 missing from manifest")
    base_dur = float(m.get("duration_ms") or 0)
    all_srcs: set = set()

    for key, t in tiers.items():
        p = f"tier {key}: "
        f = gdir / f"audio_{key}.opus"
        if not check(f.is_file() and f.stat().st_size > 0, f"{p}audio_{key}.opus missing or empty"):
            continue
        req, act = t.get("requested_factor"), t.get("actual_factor")
        if check(isinstance(req, (int, float)) and isinstance(act, (int, float)) and act > 0,
                 f"{p}bad requested/actual factor"):
            check(abs(act - req) <= FACTOR_TOL * req,
                  f"{p}actual_factor {act:.5f} not within 0.5% of requested {req}")
            exp = base_dur / act
            dur = _probe_ms(f)
            if check(dur is not None, f"{p}ffprobe failed (audio corrupt?)"):
                check(abs(dur - exp) <= DURATION_TOL_MS,
                      f"{p}audio duration {dur:.0f} ms != manifest {exp:.0f} ms "
                      f"(tolerance {DURATION_TOL_MS:.0f})")
        else:
            act = 1.0
        drift = t.get("max_drift_ms")
        check(isinstance(drift, (int, float)) and drift <= DRIFT_MAX_MS,
              f"{p}max_drift_ms {drift!r} exceeds {DRIFT_MAX_MS:.0f} ms")
        rep.info.setdefault("max_drift_ms", {})[key] = drift

        segs = t.get("segments") or []
        sidx = [sg.get("index") for sg in segs]
        missing = sorted(set(by_idx) - set(sidx))
        extra = sorted(set(sidx) - set(by_idx))
        check(not missing, f"{p}segments missing for sentence indexes {missing[:10]}"
              f"{' ...' if len(missing) > 10 else ''}")
        check(not extra, f"{p}segments with no unfiltered sentence {extra[:10]}")
        check(len(sidx) == len(set(sidx)), f"{p}duplicate segment indexes")
        check(len(segs) == len(live),
              f"{p}{len(segs)} segments != {len(live)} unfiltered sentences in DB")
        bad = 0
        for k, sg in enumerate(segs):
            st, en = sg.get("start_ms"), sg.get("end_ms")
            if not check(isinstance(st, (int, float)) and isinstance(en, (int, float)) and en >= st,
                         f"{p}segment {sg.get('index')} has invalid start/end"):
                continue
            prev = None
            for w in sg.get("words") or []:
                all_srcs.add(w.get("timing_source"))
                ws, we = w.get("start_ms"), w.get("end_ms")
                ok = (ws is not None and we is not None and st - EPS_MS <= ws <= we <= en + EPS_MS
                      and (prev is None or ws >= prev - EPS_MS))
                if not ok:
                    bad += 1
                    if bad <= 5:
                        check(False, f"{p}segment {sg.get('index')} word time outside "
                              f"segment or non-monotonic ({ws}, {we})")
                prev = ws if ws is not None else prev
            if k + 1 < len(segs):
                nx = segs[k + 1]
                gap = (sg.get("gap_ms") or 0) / act
                if isinstance(nx.get("start_ms"), (int, float)):
                    check(nx["start_ms"] >= en + gap - EPS_MS,
                          f"{p}segment {nx.get('index')} starts {nx['start_ms']:.1f} < "
                          f"end {en:.1f} + gap {gap:.1f}")
                a, b = by_idx.get(sg.get("index")), by_idx.get(nx.get("index"))
                if a and b and a.chapter != b.chapter and sg.get("gap_ms"):
                    check(sg["gap_ms"] > GAP_DEFAULT_MS,
                          f"{p}chapter boundary after segment {sg.get('index')} has gap "
                          f"{sg['gap_ms']} ms, not longer than {GAP_DEFAULT_MS} ms")
        if segs and base_dur:
            last_end = segs[-1].get("end_ms") or 0
            check(abs(last_end - base_dur / act) <= DURATION_TOL_MS,
                  f"{p}last segment end {last_end:.0f} ms != duration {base_dur / act:.0f} ms")

    all_srcs.discard(None)
    check(m.get("timing_quality") == _quality(all_srcs),
          f"timing_quality {m.get('timing_quality')!r} inconsistent with word timing_sources "
          f"{sorted(all_srcs)} (expected {_quality(all_srcs)!r})")
    rep.info["timing_quality"] = m.get("timing_quality")
    return rep


def _bind_engine() -> bool:
    """Standalone runs have no app lifespan, so ``_db.engine`` is None. Bind a
    plain engine to the existing DB (DB_PATH honoured); never create or migrate."""
    if _db.engine is not None:
        return True
    if not Path(_db._DEFAULT_DB).is_file():
        return False
    from sqlmodel import create_engine
    _db.engine = create_engine(f"sqlite:///{_db._DEFAULT_DB}")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("book_id")
    ap.add_argument("--generation", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not _bind_engine():
        print(f"database not found: {_db._DEFAULT_DB}", file=sys.stderr)
        return 2
    rep = verify_render(args.book_id, args.generation)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
    elif rep.ok:
        print(f"OK  book={rep.book_id} generation={rep.generation} ({rep.checks} checks)")
    else:
        print(f"FAIL  book={rep.book_id} generation={rep.generation}: "
              f"{len(rep.failures)} failure(s) of {rep.checks} checks")
        for f in rep.failures:
            print(f"  - {f}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
