"""Capture and compare render "goldens" so refactors cannot silently change audio or sync.

Usage (from ``backend/``):

    uv run python scripts/render_baseline.py capture --engine piper --out .baseline/piper.json
    uv run python scripts/render_baseline.py compare --engine piper --golden .baseline/piper.json

``capture`` renders a fixed corpus through the REAL pipeline (``render_worker.render_book``,
real engine, real CTC aligner, real ffmpeg) in a throwaway DB and renders directory, then
records everything a listener or the highlighter depends on: per-sentence segments, word
timings and their source, tier files and the manifest. ``compare`` renders again and diffs
against the golden. Goldens are machine-specific and live outside git (``.baseline/``).

Real engines are NOT bit-reproducible (VITS noise, torch kernels: two renders of identical code
differ), so for them ``compare`` is statistical: same structure and word counts, per-sentence
and total durations within a percentage, words ordered and inside their segment. The ``fake``
engine (deterministic tone + uniform aligner) renders through the same real pipeline and is
compared EXACTLY, which is what proves ordering, chunking, tiers and the manifest unchanged.

Exit code: 0 = identical within tolerance, 1 = differences, 2 = could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import db.database as _db  # noqa: E402
import storage  # noqa: E402
from db.models import Book, Sentence  # noqa: E402

SR = 24000
TIERS = (1.5, 2.0)
BOOK_ID = "baseline-book"
FINGERPRINT = "baseline-fingerprint"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Mixed lengths, numbers, abbreviations, quotes and punctuation: the cases where
# tokenisation and alignment differ most.
CORPUS = [
    "The quick brown fox jumps over the lazy dog.",
    "Dr. Smith arrived at 9:30 a.m. and left before noon.",
    "\"Well,\" she said, \"that is not what I expected.\"",
    "In 1999, the company sold 2,500 units for $14.99 each.",
    "Short one.",
    "It was a bright cold day in April, and the clocks were striking thirteen.",
    "Why would anyone build a bridge there? Nobody knew.",
    "The results (see Table 3) were statistically significant, p < 0.05.",
    "She whispered a name that no one in the room had heard before.",
    "He walked slowly down the long corridor, past rows of silent doors.",
    "Numbers like 3.14159 and 42 are read digit by digit or as words.",
    "Rain fell steadily on the tin roof, and the whole house hummed with it.",
    "Yes.",
    "The committee met on Tuesday, reviewed the proposal, and adjourned without a vote.",
    "Colors, shapes, and sounds all blurred together in the evening light.",
    "A single candle burned at the window; the street below was empty.",
    "Mr. and Mrs. Jones live at 221B Baker Street, London.",
    "Nothing, absolutely nothing, could have prepared them for what came next.",
    "The train left at 7:45, arrived at 11:10, and was forty minutes late.",
    "Read the next chapter carefully before attempting the exercises.",
    "Page 14",
    "At dawn the fishermen pushed their boats into the grey water and did not speak.",
    "Copyright 2024 All rights reserved.",
    "Wait, what was that noise? Nobody moved, and nobody breathed.",
    "By the end of the summer, the orchard had given more fruit than anyone could carry.",
]
# Sentences the pipeline must skip (the real text filter would drop them): they exercise the
# "index gaps" case that remapping and the manifest have to survive.
FILTERED = {"Page 14", "Copyright 2024 All rights reserved."}
CHAPTER_SIZE = 8  # sentences per chapter, so chapter breaks (extra gap) are exercised

VOICES = {"piper": "en_US-lessac-medium", "kokoro-cpu": "af_heart", "fake": "af_heart",
          "piper-replay": "en_US-lessac-medium", "kokoro-cpu-replay": "af_heart",
          "kokoro-onnx": "af_heart", "kokoro-onnx-replay": "af_heart",
          "kokoro-gpu": "af_heart"}
# ``*-replay`` engines feed the PCM recorded at capture time back through the REAL aligner and
# pipeline, so everything downstream of synthesis is deterministic and compared exactly.
EXACT_ENGINES = {"fake", "piper-replay", "kokoro-cpu-replay", "kokoro-onnx-replay"}
PCM_ROOT = Path(__file__).resolve().parent.parent / ".baseline" / "pcm"

# Tolerances for compare. CPU engines on one machine are expected to be exact; the
# tolerances exist so a deliberate, small numerical difference (GPU, ONNX) is reported
# as a number instead of a hard failure. ``exact`` mode forces zero.
TOL = {
    "segment_frac": 0.03,   # per-sentence duration, relative
    "total_frac": 0.01,     # whole-book duration, relative
    "word_order_slack_ms": 1.0,
    "size_frac": 0.05,      # encoded file size, relative
    "word_pos_frac": 0.06,  # a word's relative position inside its sentence
}
# Measured run-to-run noise on UNCHANGED code (two renders, same machine): Piper's VITS
# duration predictor is stochastic, so a single sentence moved 8.6% then 15.1% while the whole
# book stayed within 1%. Kokoro CPU lengths were identical. The per-sentence bound is therefore
# loose for Piper; the word-count, order and containment checks are what protect highlighting.
# Word position inside a sentence moved 0.063 on unchanged Piper code (Kokoro: 0.003).
# Even so, one captured Piper sample cannot gate word position reliably (it flaked in ~1 of 3 runs on
# unchanged code, bimodally: one word at 0.67 vs 0.54), so for live Piper it is ADVISORY: reported as
# a note, never a failure (per-sentence duration too: a 2-word sentence moved 27%). Word text, counts,
# ordering, containment and the whole-book duration stay hard gates, and
# `piper-replay` gates the real aligner path exactly.
ENGINE_TOL = {"piper": {"segment_frac": 0.25, "total_frac": 0.02, "word_pos_frac": 0.12,
                        "word_pos_advisory": True, "segment_advisory": True},
              # Kokoro ONNX fp32 is deterministic on one machine/thread count, but thread
              # count changes float summation order, so live runs get a small statistical
              # tolerance; `kokoro-onnx-replay` gates the aligner path exactly.
              "kokoro-onnx": {"segment_frac": 0.05, "total_frac": 0.02, "word_pos_frac": 0.08}}


def tol_for(engine: str) -> dict:
    return {**TOL, **ENGINE_TOL.get(engine, {})}


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, msg: str) -> None:
        self.failures.append(msg)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pcm_sha(path: Path) -> str:
    """Hash of the decoded PCM, so container metadata differences cannot hide or fake a diff."""
    data, _ = sf.read(path, dtype="int16")
    return hashlib.sha256(np.ascontiguousarray(data).tobytes()).hexdigest()


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _base_engine(name: str) -> str:
    return name[:-len("-replay")] if name.endswith("-replay") else name


class RecordingSynth:
    """Wraps a real synthesizer and saves each sentence's PCM + native timings for replay."""

    def __init__(self, inner: Callable, out_dir: Path):
        self.inner, self.dir = inner, out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, text: str, voice: str):
        res = self.inner(text, voice)
        k = _key(text)
        np.save(self.dir / f"{k}.npy", np.asarray(res.audio, dtype=np.float32))
        (self.dir / f"{k}.json").write_text(json.dumps({
            "effective_speed": res.effective_speed, "native_words": res.native_words,
            "kokoro_tokens": res.kokoro_tokens, "text": text}))
        return res


class ReplaySynth:
    """Replays recorded PCM: deterministic input for the real aligner and encoder."""

    parallel_safe = True  # stateless: lets RENDER_SYNTH_WORKERS>1 exercise the parallel path

    def for_worker(self, threads=None):
        return self

    def __init__(self, src_dir: Path):
        if not src_dir.is_dir():
            raise RuntimeError(f"no recorded PCM in {src_dir}; run capture for the base engine first")
        self.dir = src_dir

    def __call__(self, text: str, voice: str):
        from services import render_worker as rw

        k = _key(text)
        meta = json.loads((self.dir / f"{k}.json").read_text())
        return rw.SynthResult(np.load(self.dir / f"{k}.npy"), meta["effective_speed"],
                              meta["native_words"], meta["kokoro_tokens"])


class FakeSynth:
    """Deterministic: a 220 Hz tone, 20 ms per character, with a text-dependent onset."""

    parallel_safe = True  # stateless: lets RENDER_SYNTH_WORKERS>1 exercise the parallel path

    def for_worker(self, threads=None):
        return self

    def __call__(self, text: str, voice: str):
        from services import render_worker as rw

        n = int(SR * 0.02 * len(text))
        t = np.arange(n) / SR
        a = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        a[:240] *= 0.2 + (len(text) % 5) * 0.1
        return rw.SynthResult(a, 1.0)


class FakeAligner:
    """Uniform word spacing: deterministic and independent of the audio content."""

    def align(self, audio, sr, words):
        dur = len(audio) / sr * 1000
        step = dur / len(words)
        return [(i * step, (i + 1) * step, 0.9) for i in range(len(words))]


def build_engine(name: str) -> Any:
    """A ``TTSEngine`` around the requested real engine."""
    from services import engine_manager
    from services.tts_engine import TTSEngine

    if name == "piper":
        built, err = engine_manager.build_piper()
    elif name == "kokoro-cpu":
        built, err = engine_manager.build_local("cpu")
    elif name == "kokoro-gpu":
        built, err = engine_manager.build_local("cuda")
    elif name == "kokoro-onnx":
        built, err = engine_manager.build_kokoro_onnx()
    else:
        raise SystemExit(f"unknown engine {name!r}; expected one of {', '.join(VOICES)}")
    if built is None:
        raise RuntimeError(f"engine {name} unavailable: {err}")
    return TTSEngine(built)


def make_sandbox(root: Path, corpus: list[str], voice: str):
    """Point the app at a throwaway DB and renders dir; return the engine."""
    engine = create_engine(f"sqlite:///{root / 'baseline.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    _db.engine = engine
    (root / "renders").mkdir(parents=True, exist_ok=True)
    storage.RENDERS_DIR = root / "renders"
    with Session(engine) as s:
        s.add(Book(id=BOOK_ID, title=BOOK_ID, file_path="missing.txt", file_type="text",
                   page_count=1, created_at=T0, target_fingerprint=FINGERPRINT,
                   job_status="pending", voice=voice))
        s.add_all(Sentence(book_id=BOOK_ID, index=i, text=t, page=0, x0=0, y0=0, x1=0, y1=0,
                           filtered=t in FILTERED, chapter=i // CHAPTER_SIZE)
                  for i, t in enumerate(corpus))
        s.commit()
    return engine


def render_once(engine_name: str, corpus: list[str], root: Path,
                render_fn: Callable[..., str] | None = None, *, record: bool = False,
                ) -> tuple[Path, dict]:
    """Run the real pipeline for the corpus.

    Returns ``(generation_dir, aligned)`` where ``aligned`` maps sentence text to the word
    list the aligner produced (word, start_ms, end_ms, timing_source), before clamping.
    """
    from services import render_jobs as rj
    from services import render_worker as rw

    voice = VOICES[engine_name]
    make_sandbox(root, corpus, voice)
    aligner = None
    if engine_name == "fake":
        synth, aligner = FakeSynth(), FakeAligner()
    elif engine_name.endswith("-replay"):
        synth = ReplaySynth(PCM_ROOT / _base_engine(engine_name))
    else:
        synth = rw.engine_synthesizer(build_engine(engine_name))
        if record:
            synth = RecordingSynth(synth, PCM_ROOT / engine_name)
    now = datetime.now(timezone.utc).replace(year=2030)
    claimed = rj.claim_next("baseline", now, lease_s=3600, debounce_s=0)
    if claimed != BOOK_ID:
        raise RuntimeError(f"could not claim the baseline book (got {claimed!r})")

    aligned: dict[str, list] = {}
    original = rw.align_sentence

    def spy(audio, sr, text, **kw):
        out = original(audio, sr, text, **kw)
        aligned[text] = [[t.word, t.start_ms, t.end_ms, t.timing_source] for t in out]
        return out

    rw.align_sentence = spy
    try:
        outcome = (render_fn or rw.render_book)(
            BOOK_ID, "baseline", synthesizer=synth, aligner=aligner, tiers=TIERS,
            now=lambda: now, lease_s=3600, raise_errors=True)
    finally:
        rw.align_sentence = original
    if outcome != rw.READY:
        raise RuntimeError(f"render ended {outcome!r}, expected {rw.READY!r}")
    gens = sorted(p for p in (storage.RENDERS_DIR / BOOK_ID).iterdir() if p.name.isdigit())
    return gens[-1], aligned


def snapshot(gen_dir: Path, engine_name: str, corpus: list[str], aligned: dict | None = None) -> dict:
    """Everything a listener or the highlighter depends on, from the published files."""
    from scripts.verify_render import verify_render  # noqa: E402  (after sandbox is set)

    manifest = json.loads((gen_dir / "manifest.json").read_text())
    files = {}
    for p in sorted(gen_dir.iterdir()):
        if p.is_file() and p.suffix in (".opus", ".flac", ".wav"):
            files[p.name] = {"sha256": _sha(p), "pcm_sha256": _pcm_sha(p),
                             "bytes": p.stat().st_size}
    report = verify_render(BOOK_ID, int(gen_dir.name))
    return {
        "engine": engine_name,
        "corpus_sha256": hashlib.sha256("\n".join(corpus).encode()).hexdigest(),
        "manifest": _scrub(manifest),
        "aligned": aligned or {},
        "meta": _meta(),
        "files": files,
        "verify": report.to_dict(),
    }


def _meta() -> dict:
    """Provenance, so a golden can be tied to the code and libraries that produced it."""
    import subprocess
    from importlib.metadata import PackageNotFoundError, version

    def ver(pkg: str) -> str | None:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return None

    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent).stdout.strip() or None
    except OSError:
        head = None
    return {"git_head": head, "torch": ver("torch"), "piper_tts": ver("piper-tts"), "onnxruntime": ver("onnxruntime"),
            "onnxruntime": ver("onnxruntime"), "kokoro": ver("kokoro")}


_VOLATILE = {"created_at", "rendered_at", "built_at", "generated_at", "timestamp"}


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _segments(manifest: dict, tier: str) -> list[dict]:
    return manifest.get("tiers", {}).get(tier, {}).get("segments", [])


def _diff_manifest(g: dict, f: dict, rep: Report, *, exact: bool, tol: dict) -> None:
    for key in ("voice", "fingerprint", "sample_rate", "gap_policy", "container",
                "base_effective_speed", "mixed_backends", "schema"):
        if g.get(key) != f.get(key):
            rep.fail(f"manifest.{key}: {g.get(key)!r} -> {f.get(key)!r}")
    if exact and g.get("duration_ms") != f.get("duration_ms"):
        rep.fail(f"manifest.duration_ms: {g.get('duration_ms')} -> {f.get('duration_ms')}")
    gd, fd = float(g.get("duration_ms", 0)), float(f.get("duration_ms", 0))
    if not exact and gd and abs(fd - gd) / gd > tol["total_frac"]:
        rep.fail(f"total duration {gd:.0f} -> {fd:.0f} ms "
                 f"({abs(fd - gd) / gd:.2%} > {tol['total_frac']:.0%})")
    gt, ft = g.get("tiers", {}), f.get("tiers", {})
    if list(gt) != list(ft):
        rep.fail(f"tier keys/order differ: {list(gt)} -> {list(ft)}")
    worst_seg = 0.0
    words_total = 0
    for tier in [t for t in gt if t in ft]:
        for fld in ("requested_factor",):
            if gt[tier].get(fld) != ft[tier].get(fld):
                rep.fail(f"tier {tier}.{fld}: {gt[tier].get(fld)} -> {ft[tier].get(fld)}")
        if exact and gt[tier] != ft[tier]:
            rep.fail(f"tier {tier} differs (exact mode)")
        gs, fs = _segments(g, tier), _segments(f, tier)
        if [x["index"] for x in gs] != [x["index"] for x in fs]:
            rep.fail(f"tier {tier}: segment index sequence differs")
            continue
        for a, b in zip(gs, fs):
            words_total += len(b["words"])
            if len(a["words"]) != len(b["words"]):
                rep.fail(f"tier {tier} sentence {a['index']}: word count "
                         f"{len(a['words'])} -> {len(b['words'])}")
            if a.get("gap_ms") != b.get("gap_ms"):
                rep.fail(f"tier {tier} sentence {a['index']}: gap_ms "
                         f"{a.get('gap_ms')} -> {b.get('gap_ms')}")
            ad, bd = a["end_ms"] - a["start_ms"], b["end_ms"] - b["start_ms"]
            frac = abs(bd - ad) / ad if ad else 0.0
            worst_seg = max(worst_seg, frac)
            if not exact and frac > tol["segment_frac"]:
                rep.fail(f"tier {tier} sentence {a['index']}: duration {ad:.0f} -> {bd:.0f} ms "
                         f"({frac:.1%} > {tol['segment_frac']:.0%})")
            ag = sorted(w.get("timing_source") or "" for w in a["words"])
            bg = sorted(w.get("timing_source") or "" for w in b["words"])
            if exact and ag != bg:
                rep.fail(f"tier {tier} sentence {a['index']}: timing sources differ")
            _check_words(b, tier, rep, tol)
    rep.notes.append(f"{words_total} word entries checked, worst per-sentence duration "
                     f"change {worst_seg:.2%}")


def _check_words(seg: dict, tier: str, rep: Report, tol: dict) -> None:
    """Highlighting invariants that must hold for ANY render: ordered, inside the segment."""
    prev_end = seg["start_ms"]
    slack = tol["word_order_slack_ms"]
    for i, w in enumerate(seg["words"]):
        if w["end_ms"] + slack < w["start_ms"]:
            rep.fail(f"tier {tier} sentence {seg['index']} word {i}: ends before it starts")
        if w["start_ms"] + slack < prev_end:
            rep.fail(f"tier {tier} sentence {seg['index']} word {i}: overlaps previous word")
        if w["start_ms"] + slack < seg["start_ms"] or w["end_ms"] > seg["end_ms"] + slack:
            rep.fail(f"tier {tier} sentence {seg['index']} word {i}: outside its sentence")
        prev_end = w["end_ms"]


def _norm(w: str) -> str:
    return "".join(ch for ch in w.lower() if ch.isalnum())


def _check_aligned(golden: dict, fresh: dict, rep: Report, *, exact: bool, tol: dict) -> None:
    """Word TEXT and timings per sentence: what the highlighter actually shows."""
    ga, fa = golden.get("aligned", {}), fresh.get("aligned", {})
    if not ga:
        rep.notes.append("golden has no aligned words (captured before this check existed)")
        return
    if set(ga) != set(fa):
        rep.fail(f"sentence set differs: {sorted(set(ga) ^ set(fa))[:3]}")
    shifts = []
    for text in sorted(set(ga) & set(fa)):
        g, f = ga[text], fa[text]
        if [w[0] for w in g] != [w[0] for w in f]:
            rep.fail(f"words differ for {text[:40]!r}: {[w[0] for w in g][:4]} -> "
                     f"{[w[0] for w in f][:4]}")
            continue
        if [_norm(w[0]) for w in f] != [_norm(x) for x in text.split()]:
            rep.fail(f"aligned words do not spell the sentence {text[:40]!r}")
        if exact:
            if g != f:
                rep.fail(f"word timings/sources differ for {text[:40]!r}")
            continue
        # Position within the SPOKEN span (first word start -> last word end), so a different
        # amount of leading silence (VITS varies it run to run) cannot register as words moving.
        g0, f0 = g[0][1], f[0][1]
        gd, fd = max(g[-1][2] - g0, 1), max(f[-1][2] - f0, 1)
        if len(g) < 2:
            continue  # a single word has no internal position to compare
        for i, (a, b) in enumerate(zip(g, f)):
            ra, rb_ = (a[1] - g0) / gd, (b[1] - f0) / fd
            shifts.append(abs(ra - rb_))
            if abs(ra - rb_) > tol["word_pos_frac"]:
                msg = (f"{text[:40]!r} word {i} {a[0]!r}: relative start "
                       f"{ra:.2f} -> {rb_:.2f}")
                if tol.get("word_pos_advisory"):
                    rep.notes.append("advisory: " + msg)
                else:
                    rep.fail(msg)
    if shifts:
        rep.notes.append(f"worst relative word-position shift {max(shifts):.3f} "
                         f"(limit {tol['word_pos_frac']})")


def _check_tiers(manifest: dict, rep: Report) -> None:
    """Properties of ONE render: sentences ordered per tier, tiers consistent with the base."""
    tiers = manifest.get("tiers", {})
    for key, info in tiers.items():
        segs = info["segments"]
        for a, b in zip(segs, segs[1:]):
            if not a["end_ms"] <= b["start_ms"] + 1:
                rep.fail(f"tier {key}: sentence {a['index']} ends after {b['index']} starts")
            if not a["index"] < b["index"]:
                rep.fail(f"tier {key}: sentence order broken at {a['index']}, {b['index']}")
    base = tiers.get("1.0")
    if not base:
        return
    for key, info in tiers.items():
        if key == "1.0":
            continue
        factor = info["actual_factor"]
        for bs, ts in zip(base["segments"], info["segments"]):
            expect = bs["start_ms"] / factor
            if abs(ts["start_ms"] - expect) > 0.02 * expect + 40:
                rep.fail(f"tier {key} sentence {ts['index']}: start {ts['start_ms']:.0f} ms but "
                         f"base/factor says {expect:.0f} ms")


def diff_snapshots(golden: dict, fresh: dict, *, exact: bool = False, engine: str = "") -> Report:
    """Compare two snapshots. ``exact`` (fake engine) demands identical PCM and manifest."""
    rep = Report()
    tol = tol_for(engine or fresh.get("engine", ""))
    if golden.get("corpus_sha256") != fresh.get("corpus_sha256"):
        rep.fail("corpus differs between golden and fresh render")
        return rep
    if not fresh["verify"]["ok"]:
        rep.fail(f"verify_render failed: {fresh['verify']['failures'][:3]}")
    if set(golden["files"]) != set(fresh["files"]):
        rep.fail(f"file set differs: {sorted(set(golden['files']) ^ set(fresh['files']))}")
    for name in sorted(set(golden["files"]) & set(fresh["files"])):
        g, f = golden["files"][name], fresh["files"][name]
        if exact:
            if g["pcm_sha256"] != f["pcm_sha256"]:
                rep.fail(f"{name}: decoded PCM differs")
        elif g["bytes"] and abs(f["bytes"] - g["bytes"]) / g["bytes"] > tol["size_frac"]:
            rep.fail(f"{name}: size {g['bytes']} -> {f['bytes']} "
                     f"(> {tol['size_frac']:.0%})")
    _diff_manifest(golden["manifest"], fresh["manifest"], rep, exact=exact, tol=tol)
    _check_tiers(fresh["manifest"], rep)
    _check_aligned(golden, fresh, rep, exact=exact, tol=tol)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("capture", "compare"))
    ap.add_argument("--engine", choices=sorted(VOICES), required=True)
    ap.add_argument("--golden", "--out", dest="golden", required=True, type=Path)
    ap.add_argument("--sentences", type=int, default=len(CORPUS),
                    help="use only the first N corpus sentences (default: all)")
    ap.add_argument("--keep", type=Path, default=None, metavar="DIR",
                    help="copy the rendered files (opus tiers, manifest) into DIR so you can listen")
    ap.add_argument("--exact", action="store_true",
                    help="force exact comparison (default: exact only for the fake engine)")
    args = ap.parse_args(argv)

    corpus = CORPUS[: args.sentences]
    root = Path(tempfile.mkdtemp(prefix="render-baseline-"))
    try:
        try:
            gen, aligned = render_once(args.engine, corpus, root,
                                       record=args.mode == "capture")
            fresh = snapshot(gen, args.engine, corpus, aligned)
            if args.keep:
                args.keep.mkdir(parents=True, exist_ok=True)
                for f in gen.iterdir():
                    if f.is_file():
                        shutil.copy2(f, args.keep / f.name)
                (args.keep / "corpus.txt").write_text("\n".join(corpus) + "\n")
                print(f"kept render files in {args.keep}")
        except Exception as exc:  # noqa: BLE001
            print(f"could not render: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if args.mode == "capture":
            args.golden.parent.mkdir(parents=True, exist_ok=True)
            args.golden.write_text(json.dumps(fresh, indent=1, sort_keys=True))
            print(f"captured {args.golden} ({len(fresh['files'])} audio files, "
                  f"verify ok={fresh['verify']['ok']})")
            return 0 if fresh["verify"]["ok"] else 1
        golden = json.loads(args.golden.read_text())
        rep = diff_snapshots(golden, fresh, exact=args.exact or args.engine in EXACT_ENGINES,
                             engine=args.engine)
        for n in rep.notes:
            print(n)
        for m in rep.failures:
            print("FAIL:", m)
        print("OK: identical to baseline" if rep.ok else f"{len(rep.failures)} difference(s)")
        return 0 if rep.ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
