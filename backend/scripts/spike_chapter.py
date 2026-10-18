"""Phase 1a spike: full-chapter (500+ sentence) atempo drift, aligner comparison, disk use.

Subcommands (all share --work DIR, default backend/scripts/output/spike_chapter):

    text                         pick prose sentences from an existing book in the app DB
    synth  {kokoro,piper}        synthesize the chapter at 1x -> base.flac + per-sentence arrays
    drift  {kokoro,piper}        marker-tone drift for every atempo tier + disk use
    align  {kokoro,piper} {fw,ctc}   aligner real-time factor + 0.50 gate pass rate

Run from backend/:  uv run python scripts/spike_chapter.py <subcommand> ...

Nothing here is wired into the app; it is measurement code for
docs/plans/process-first-architecture.md ("Spike results (Phase 1a)").
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 24000
GAP_MS = 120                       # plan: 120 ms between sentences
TIERS = (0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
OPUS_KBPS = 24                     # same default as atempo_tiers.py
MARK_HZ = 5000.0                   # marker tone frequency
MARK_MS = 40.0                     # marker tone length (centered in the 120 ms gap)
GATE = 0.50
DEFAULT_DB = Path("/home/christapia50/Repos/ebook-reader/backend/ebook_reader.db")
DEFAULT_BOOK = "f21271552122370afb7f2df3721bcd426efde1df1379ad9e829758fa02289ed2"  # Buddha's Brain (prose)
PIPER_VOICES = "/home/christapia50/Repos/ebook-reader/backend/voices/piper"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def atempo_chain(factor: float) -> str:
    parts, f = [], factor
    while f > 2.0:
        parts.append(2.0)
        f /= 2.0
    while f < 0.5:
        parts.append(0.5)
        f /= 0.5
    parts.append(f)
    return ",".join(f"atempo={p:.6f}" for p in parts)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def encode_opus(src: Path, dst: Path, factor: float | None) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if factor is not None and abs(factor - 1.0) > 1e-9:
        cmd += ["-filter:a", atempo_chain(factor)]
    cmd += ["-ar", str(SR), "-ac", "1", "-c:a", "libopus", "-b:a", f"{OPUS_KBPS}k", str(dst)]
    run(cmd)


def decode(src: Path) -> np.ndarray:
    tmp = src.with_suffix(".dec.wav")
    run(["ffmpeg", "-y", "-i", str(src), "-ar", str(SR), "-ac", "1", str(tmp)])
    x, _ = sf.read(tmp, dtype="float32")
    tmp.unlink()
    return x


def norm_words(text: str) -> list[str]:
    return [w for w in (re.sub(r"[^a-z0-9']", "", t.lower()) for t in text.split()) if w]


def similarity(known: str, heard: str) -> float:
    """The one fuzzy text matcher (character-level ratio over normalized words)."""
    a, b = " ".join(norm_words(known)), " ".join(norm_words(heard))
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def map_words(known: list[str], heard: list[str]) -> dict[int, int]:
    """known word index -> heard word index (exact blocks, then 1:1 fuzzy inside replace blocks)."""
    sm = difflib.SequenceMatcher(None, known, heard, autojunk=False)
    out: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out[i1 + k] = j1 + k
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                if difflib.SequenceMatcher(None, known[i1 + k], heard[j1 + k]).ratio() >= 0.5:
                    out[i1 + k] = j1 + k
    return out


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


# ----------------------------------------------------------------------------
# text
# ----------------------------------------------------------------------------
def cmd_text(a) -> None:
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = con.execute('select "index", text from sentence where book_id=? order by "index"', (a.book,)).fetchall()
    picked: list[dict] = []
    for idx, t in rows:
        if idx < a.start:
            continue
        t = " ".join(t.split())
        letters = sum(c.isalpha() for c in t)
        if not (40 <= len(t) <= 260 and t[-1] in ".?!\"”" and letters / len(t) > 0.8 and t[0].isupper()):
            continue
        picked.append({"index": idx, "text": t})
        if len(picked) >= a.n:
            break
    a.work.mkdir(parents=True, exist_ok=True)
    (a.work / "text.json").write_text(json.dumps({"book": a.book, "sentences": picked}, indent=1))
    words = sum(len(p["text"].split()) for p in picked)
    print(f"picked {len(picked)} sentences, {words} words, source index {picked[0]['index']}..{picked[-1]['index']}")


# ----------------------------------------------------------------------------
# synth
# ----------------------------------------------------------------------------
PUNCT = set(",.;:!?\u2014\u2013-\"'()\u201c\u201d\u2018\u2019\u2026")


def piper_words_from_chunks(chunks, rate):
    """Word (start_s, end_s) from phoneme runs between spaces; also total aligned samples."""
    t, words, cur_s, cur_e, total = 0, [], None, None, 0
    for ch in chunks:
        for pa in ch.phoneme_alignments or []:
            n = int(pa.num_samples)
            total += n
            ph = pa.phoneme
            if ph in ("^", "$"):
                pass
            elif ph == " " or ph in PUNCT:
                if cur_s is not None:
                    words.append((cur_s / rate, cur_e / rate)); cur_s = cur_e = None
            else:
                if cur_s is None:
                    cur_s = t
                cur_e = t + n
            t += n
    if cur_s is not None:
        words.append((cur_s / rate, cur_e / rate))
    return words, total


def load_piper(name, align):
    from piper.voice import PiperVoice
    return PiperVoice.load(f"{PIPER_VOICES}/{name}.onnx", include_alignments=align)


def synth_piper(a, sents, d, voice):
    from piper.config import SynthesisConfig
    from services.piper_engine import _resample, _prevent_clipping
    v = load_piper(voice, True)
    rate = int(v.config.sample_rate)
    cfg = SynthesisConfig(length_scale=1.0)
    arrays, pw = [], []
    t0 = time.perf_counter()
    for i, s in enumerate(sents):
        chunks = list(v.synthesize(s["text"], syn_config=cfg, include_alignments=True))
        native = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        words, total = piper_words_from_chunks(chunks, rate)
        pw.append({"words": words, "aligned_samples": total, "audio_samples": int(len(native)),
                   "all_chunks_aligned": all(c.phoneme_alignments for c in chunks), "n_chunks": len(chunks)})
        arrays.append(_resample(native, rate, SR).astype(np.float32))
        if i % 50 == 0:
            print(f"  {i}/{len(sents)} synth elapsed {time.perf_counter() - t0:.0f}s", flush=True)
    synth_s = time.perf_counter() - t0
    finish_base(a, d, arrays, voice, synth_s, {"native_tokens": [[] for _ in arrays], "piper_native": pw, "piper_rate": rate})


def cmd_piperbench(a) -> None:
    """RTF of Piper synthesis with vs without alignments, alternating order, nothing else running."""
    from piper.config import SynthesisConfig
    sents = json.loads((a.work / "text.json").read_text())["sentences"][: a.limit or None]
    cfg = SynthesisConfig(length_scale=1.0)
    voice = "en_US-lessac-medium"
    vs = {True: load_piper(voice, True), False: load_piper(voice, False)}
    for al in (True, False):  # warm-up
        list(vs[al].synthesize(sents[0]["text"], syn_config=cfg, include_alignments=al))
    res = {True: [], False: []}
    audio = {True: 0.0, False: 0.0}
    for rep in range(a.reps):
        for al in ((True, False) if rep % 2 == 0 else (False, True)):
            t0 = time.perf_counter(); n = 0
            for s in sents:
                for c in vs[al].synthesize(s["text"], syn_config=cfg, include_alignments=al):
                    n += len(c.audio_float_array)
            wall = time.perf_counter() - t0
            res[al].append(wall / (n / vs[al].config.sample_rate))
            print(f"  rep {rep} align={al}: RTF {res[al][-1]:.4f} (wall {wall:.0f}s)", flush=True)
    out = {"sentences": len(sents), "reps": a.reps, "rtf_with_alignments": res[True], "rtf_without": res[False],
           "mean_with": float(np.mean(res[True])), "mean_without": float(np.mean(res[False]))}
    (a.work / "piper" / "bench.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def finish_base(a, d, arrays, voice, synth_s, extra):
    gap = np.zeros(int(SR * GAP_MS / 1000), np.float32)
    segs, chunks, cur = [], [], 0
    for i, au in enumerate(arrays):
        segs.append({"index": i, "start_ms": cur / SR * 1000, "end_ms": (cur + len(au)) / SR * 1000, "gap_ms": GAP_MS})
        chunks += [au, gap]
        cur += len(au) + len(gap)
    base = np.concatenate(chunks)
    sf.write(d / "base.flac", base, SR, format="FLAC", subtype="PCM_16")
    np.savez(d / "sentences.npz", *arrays)
    audio_s = sum(len(x) for x in arrays) / SR
    (d / "meta.json").write_text(json.dumps({
        "engine": a.engine, "voice": voice, "n": len(arrays), "segments": segs,
        "synth_wall_s": synth_s, "speech_audio_s": audio_s, "base_duration_s": len(base) / SR,
        "synth_rtf": synth_s / audio_s, "cpu": "16 threads, no GPU", **extra}))
    print(f"{a.engine}: {len(arrays)} sentences, speech {audio_s / 60:.1f} min, base {len(base) / SR / 60:.1f} min, "
          f"synth wall {synth_s / 60:.1f} min (RTF {synth_s / audio_s:.3f}), base.flac {(d / 'base.flac').stat().st_size / 1e6:.1f} MB")


def cmd_synth(a) -> None:
    sents = json.loads((a.work / "text.json").read_text())["sentences"]
    d = a.work / a.engine
    d.mkdir(parents=True, exist_ok=True)
    if a.engine == "kokoro":
        from services import engine_manager
        eng, err = engine_manager.build_local("cpu")
        if eng is None:
            raise SystemExit(err)
        voice = a.voice or "af_heart"
    else:
        voice = a.voice or "en_US-lessac-medium"
        return synth_piper(a, sents, d, voice)

    arrays, native = [], []
    t0 = time.perf_counter()
    for i, s in enumerate(sents):
        parts, toks = [], []
        for r in eng(s["text"], voice=voice, speed=1.0):
            au = r[-1]
            au = au.numpy() if hasattr(au, "numpy") else np.asarray(au)
            off = sum(len(p) for p in parts) / SR
            for t in (getattr(r, "tokens", None) or []):
                if getattr(t, "start_ts", None) is not None and getattr(t, "end_ts", None) is not None and t.text.strip(" .,;:!?\"'-"):
                    toks.append({"w": t.text, "s": t.start_ts + off, "e": t.end_ts + off})
            parts.append(au.astype(np.float32).ravel())
        arrays.append(np.concatenate(parts) if parts else np.zeros(0, np.float32))
        native.append(toks)
        if i % 50 == 0:
            print(f"  {i}/{len(sents)} synth elapsed {time.perf_counter() - t0:.0f}s", flush=True)
    synth_s = time.perf_counter() - t0

    gap = np.zeros(int(SR * GAP_MS / 1000), np.float32)
    segs, chunks, cur = [], [], 0
    for i, au in enumerate(arrays):
        segs.append({"index": i, "start_ms": cur / SR * 1000, "end_ms": (cur + len(au)) / SR * 1000, "gap_ms": GAP_MS})
        chunks += [au, gap]
        cur += len(au) + len(gap)
    base = np.concatenate(chunks)
    sf.write(d / "base.flac", base, SR, format="FLAC", subtype="PCM_16")
    np.savez(d / "sentences.npz", *arrays)
    audio_s = sum(len(x) for x in arrays) / SR
    (d / "meta.json").write_text(json.dumps({
        "engine": a.engine, "voice": voice, "n": len(arrays), "segments": segs, "native_tokens": native,
        "synth_wall_s": synth_s, "speech_audio_s": audio_s, "base_duration_s": len(base) / SR,
        "synth_rtf": synth_s / audio_s, "cpu": "16 threads, no GPU"}))
    print(f"{a.engine}: {len(arrays)} sentences, speech {audio_s / 60:.1f} min, base {len(base) / SR / 60:.1f} min, "
          f"synth wall {synth_s / 60:.1f} min (RTF {synth_s / audio_s:.3f}), base.flac {(d / 'base.flac').stat().st_size / 1e6:.1f} MB")


# ----------------------------------------------------------------------------
# drift + disk
# ----------------------------------------------------------------------------
def make_marker_base(d: Path) -> tuple[Path, np.ndarray]:
    x, _ = sf.read(d / "base.flac", dtype="float32")
    meta = json.loads((d / "meta.json").read_text())
    n = int(SR * MARK_MS / 1000)
    tone = (0.6 * np.hanning(n) * np.sin(2 * np.pi * MARK_HZ * np.arange(n) / SR)).astype(np.float32)
    centers = []
    for s in meta["segments"]:
        c = int((s["end_ms"] + s["gap_ms"] / 2) / 1000 * SR)   # middle of the gap
        x[c - n // 2: c - n // 2 + n] += tone
        centers.append(c / SR * 1000)
    p = d / "base_markers.flac"
    sf.write(p, np.clip(x, -1, 1), SR, format="FLAC", subtype="PCM_16")
    return p, np.array(centers)


def detect_markers(y: np.ndarray, expected_ms: np.ndarray, half_window_ms: float = 150.0) -> tuple[np.ndarray, int]:
    t = np.arange(len(y)) / SR
    z = y * np.exp(-2j * np.pi * MARK_HZ * t)
    k = int(SR * 0.004)
    env = np.abs(np.convolve(z, np.ones(k) / k, mode="same"))
    found, bad = [], 0
    for e in expected_ms:
        lo, hi = int(max(0, e - half_window_ms) / 1000 * SR), int(min(len(y) / SR * 1000, e + half_window_ms) / 1000 * SR)
        w = env[lo:hi]
        pk = w.max()
        floor = np.median(w)
        if pk < 6 * floor + 0.01 or np.argmax(w) in (0, len(w) - 1):
            bad += 1
        m = w > 0.5 * pk
        idx = np.flatnonzero(m)
        # contiguous region around the peak
        a0 = np.argmax(w)
        l = a0
        while l > 0 and m[l - 1]:
            l -= 1
        r = a0
        while r < len(w) - 1 and m[r + 1]:
            r += 1
        seg = np.arange(l, r + 1)
        c = float((seg * w[seg]).sum() / w[seg].sum())
        found.append((lo + c) / SR * 1000)
    return np.array(found), bad


def cmd_drift(a) -> None:
    d = a.work / a.engine
    meta = json.loads((d / "meta.json").read_text())
    mbase, centers = make_marker_base(d)
    base_dur_ms = sf.info(mbase).duration * 1000
    flac_bytes = (d / "base.flac").stat().st_size
    base_hours = meta["base_duration_s"] / 3600
    out = {"engine": a.engine, "n_markers": len(centers), "base_duration_s": meta["base_duration_s"],
           "flac_bytes": flac_bytes, "flac_mb_per_hour": flac_bytes / 1e6 / base_hours, "tiers": {}}
    td = d / "tiers"
    td.mkdir(exist_ok=True)
    for tier in (1.0,) + TIERS:
        # markers version -> drift
        encode_opus(mbase, td / f"m_{tier:g}.opus", tier)
        y = decode(td / f"m_{tier:g}.opus")
        out_dur_ms = len(y) / SR * 1000
        actual = base_dur_ms / out_dur_ms
        exp_lin = centers / actual                     # linear mapping with the measured (stored) factor
        det, bad = detect_markers(y, centers / tier)   # search around the requested-factor position
        drift = det - exp_lin
        drift_req = det - centers / tier
        # clean tier -> disk
        encode_opus(d / "base.flac", td / f"t_{tier:g}.opus", tier)
        sz = (td / f"t_{tier:g}.opus").stat().st_size
        sampled = drift[::200] if len(drift) > 200 else drift
        out["tiers"][f"{tier:g}"] = {
            "requested_factor": tier, "actual_factor": actual, "factor_error_pct": (actual / tier - 1) * 100,
            "n": len(drift), "undetected_or_weak": bad,
            "max_abs_drift_ms": float(np.abs(drift).max()), "p95_abs_drift_ms": pct(np.abs(drift), 95),
            "mean_drift_ms": float(drift.mean()), "first_ms": float(drift[0]), "last_ms": float(drift[-1]),
            "first5_max_ms": float(np.abs(drift[:5]).max()), "last5_max_ms": float(np.abs(drift[-5:]).max()),
            "max_abs_drift_vs_requested_ms": float(np.abs(drift_req).max()),
            "drift_every_200th_ms": [float(v) for v in sampled],
            "opus_bytes": sz, "opus_mb_per_base_hour": sz / 1e6 / base_hours,
        }
        t = out["tiers"][f"{tier:g}"]
        print(f"{a.engine} {tier:>4}x  actual={actual:.5f} ({t['factor_error_pct']:+.3f}%)  max={t['max_abs_drift_ms']:.1f} "
              f"p95={t['p95_abs_drift_ms']:.1f} first={t['first_ms']:+.1f} last={t['last_ms']:+.1f} weak={bad}  opus={sz / 1e6:.2f}MB", flush=True)
    tot = sum(v["opus_bytes"] for v in out["tiers"].values())
    out["total_opus_bytes_all_tiers_incl_1x"] = tot
    out["total_multiple_of_flac"] = (tot + flac_bytes) / flac_bytes
    (d / "drift.json").write_text(json.dumps(out, indent=1))
    print(f"disk: FLAC {flac_bytes / 1e6:.1f} MB; opus all tiers {tot / 1e6:.1f} MB; total/FLAC = {out['total_multiple_of_flac']:.3f}")


# ----------------------------------------------------------------------------
# align
# ----------------------------------------------------------------------------
def load16(npz, i) -> np.ndarray:
    return resample_poly(npz[f"arr_{i}"], 2, 3).astype(np.float32)


def fw_align(sents, npz, threads):
    from faster_whisper import WhisperModel
    m = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=threads)
    res, wall = [], 0.0
    for i, s in enumerate(sents):
        x = load16(npz, i)
        t0 = time.perf_counter()
        segs, _ = m.transcribe(x, language="en", word_timestamps=True, beam_size=1, condition_on_previous_text=False)
        words = [(w.word.strip(), w.start, w.end) for sg in segs for w in sg.words]
        wall += time.perf_counter() - t0
        res.append(words)
        if i % 50 == 0:
            print(f"  fw {i}/{len(sents)} wall {wall:.0f}s", flush=True)
    return res, wall


def ctc_model():
    import onnxruntime as ort
    import ctc_forced_aligner as c
    p = Path.home() / "ctc_forced_aligner" / "model.onnx"
    c.ensure_onnx_model(str(p), c.MODEL_URL)
    return p


def ctc_align(sents, npz, threads, tight: bool):
    import onnxruntime as ort
    import ctc_forced_aligner as c
    p = ctc_model()
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    sess = ort.InferenceSession(str(p), so)
    tok = c.Tokenizer()
    res, wall = [], 0.0
    vocab = c.VOCAB_DICT
    for i, s in enumerate(sents):
        x = load16(npz, i)
        words = s["text"].split()
        normw = [re.sub(r"[^a-z']", "", c.text_normalize(w, "eng").lower()) for w in words]
        toks_per_word = [" ".join(ch for ch in nw if ch in vocab) for nw in normw]
        t0 = time.perf_counter()
        if tight:
            xi = ((x - x.mean()) / (x.std() + 1e-7)).astype(np.float32)[None, :]
            logits = sess.run(["logits"], {"input_values": xi})[0][0]
            lp = logits - np.log(np.exp(logits - logits.max(-1, keepdims=True)).sum(-1, keepdims=True)) - logits.max(-1, keepdims=True)
            em = np.concatenate([lp, np.zeros((lp.shape[0], 1))], 1).astype(np.float32)
            stride = 20.0
        else:
            em, stride = c.generate_emissions(sess, x, batch_size=1)
            stride = float(stride)
        starred, text_starred = [], []
        for w, tk in zip(words, toks_per_word):
            starred += ["<star>", tk]
            text_starred += ["<star>", w]
        segments, scores, blank = c.get_alignments(em, starred, tok)
        spans = c.get_spans(starred, segments, blank)
        out = c.postprocess_results(text_starred, spans, stride, scores)
        wall += time.perf_counter() - t0
        res.append([(o["text"], o["start"], o["end"], o["score"]) for o in out])
        if i % 100 == 0:
            print(f"  ctc{'(tight)' if tight else '(lib windows)'} {i}/{len(sents)} wall {wall:.0f}s", flush=True)
    return res, wall


def cmd_align(a) -> None:
    d = a.work / a.engine
    sents = json.loads((a.work / "text.json").read_text())["sentences"][: a.limit or None]
    npz = np.load(d / "sentences.npz")
    meta = json.loads((d / "meta.json").read_text())
    audio_s = sum(len(npz[f"arr_{i}"]) for i in range(len(sents))) / SR
    tag = f"{a.aligner}{'_tight' if a.aligner == 'ctc' and not a.lib_windows else ''}"
    if a.aligner == "fw":
        res, wall = fw_align(sents, npz, a.threads)
    else:
        res, wall = ctc_align(sents, npz, a.threads, tight=not a.lib_windows)
    sims, cover, rows = [], [], []
    for i, s in enumerate(sents):
        known = norm_words(s["text"])
        if a.aligner == "fw":
            heard_text = " ".join(w[0] for w in res[i])
            sim = similarity(s["text"], heard_text)
            mp = map_words(known, norm_words(heard_text))
            cov = len(mp) / max(1, len(known))
            rows.append([{"w": w[0], "s": w[1], "e": w[2]} for w in res[i]])
        else:
            # CTC aligns the KNOWN text, so there is no heard transcript to compare. Structural gate:
            # every word has non-zero duration, starts are monotonic, and mean score is not degenerate.
            ws = res[i]
            mono = all(ws[k][1] <= ws[k + 1][1] + 1e-6 for k in range(len(ws) - 1))
            durs = [w[2] - w[1] for w in ws]
            sim = 1.0 if (mono and ws and min(durs) > 0.0) else 0.0
            cov = sum(1 for dd in durs if dd > 0) / max(1, len(ws))
            rows.append([{"w": w[0], "s": w[1], "e": w[2], "score": w[3]} for w in ws])
        sims.append(sim)
        cover.append(cov)
    sims = np.array(sims)
    summary = {
        "engine": a.engine, "aligner": tag, "sentences": len(sents), "speech_audio_s": audio_s,
        "wall_s": wall, "rtf": wall / audio_s, "threads": a.threads, "device": "CPU only (no GPU)",
        "gate": GATE, "gate_pass_rate": float((sims >= GATE).mean()),
        "sim_ge_0.9_rate": float((sims >= 0.9).mean()), "sim_mean": float(sims.mean()),
        "word_coverage_mean": float(np.mean(cover)),
        "gate_kind": "whisper transcript vs known text (difflib)" if a.aligner == "fw" else "structural only (CTC aligns known text)",
        "synth_engine_meta_voice": meta["voice"],
    }
    (d / f"align_{tag}.json").write_text(json.dumps({"summary": summary, "sims": sims.tolist(), "words": rows}))
    print(json.dumps(summary, indent=1))


def cmd_compare(a) -> None:
    """Word-start agreement between aligners (and vs Kokoro native token times)."""
    d = a.work / a.engine
    sents = json.loads((a.work / "text.json").read_text())["sentences"]
    fw = json.loads((d / "align_fw.json").read_text())["words"]
    ct = json.loads((d / "align_ctc_tight.json").read_text())["words"]
    meta = json.loads((d / "meta.json").read_text())
    diffs_fc, diffs_nf, diffs_nc = [], [], []
    native_ok = []
    for i, s in enumerate(sents):
        known = norm_words(s["text"])
        cw = [x for x in ct[i]]
        cn = norm_words(" ".join(x["w"] for x in cw))
        fn = [norm_words(x["w"]) for x in fw[i]]
        fflat = [(n[0], x) for n, x in zip(fn, fw[i]) if n]
        mp = map_words([k for k in known], [f[0] for f in fflat])
        # ctc words align 1:1 with s["text"].split(); build known-index->ctc by position of non-empty norm
        ci = [k for k, x in enumerate(cw) if norm_words(x["w"])]
        cmap = {kk: cw[ci[kk]] for kk in range(min(len(ci), len(known)))}
        if "piper_native" in meta:
            # Piper native: usable only when its word count equals the text's alnum-token count; positional map.
            pwds = meta["piper_native"][i]["words"]
            texttoks = [k for k, w in enumerate(sents[i]["text"].split()) if norm_words(w)]
            nat = [{"s": w[0]} for w in pwds]
            nmp = {k: k for k in range(len(known))} if len(pwds) == len(known) == len(texttoks) else {}
            native_ok.append(bool(nmp))
        else:
            nat = [t for t in meta["native_tokens"][i] if norm_words(t["w"])]
            nmp = map_words(known, [norm_words(t["w"])[0] for t in nat]) if nat else {}
        for kk, fj in mp.items():
            if kk in cmap:
                diffs_fc.append((fflat[fj][1]["s"] - cmap[kk]["s"]) * 1000)
        for kk, nj in nmp.items():
            if kk in cmap:
                diffs_nc.append((nat[nj]["s"] - cmap[kk]["s"]) * 1000)
            if kk in mp:
                diffs_nf.append((nat[nj]["s"] - fflat[mp[kk]][1]["s"]) * 1000)

    def st(v):
        v = np.abs(np.array(v))
        return {"n": len(v), "median_abs_ms": pct(v, 50), "p95_abs_ms": pct(v, 95), "p99_abs_ms": pct(v, 99)}
    out = {"engine": a.engine, "whisper_vs_ctc_start": st(diffs_fc) if diffs_fc else None,
           "native_vs_whisper": st(diffs_nf) if diffs_nf else None,
           "native_vs_ctc": st(diffs_nc) if diffs_nc else None,
           "native_word_count_match_rate": float(np.mean(native_ok)) if native_ok else None}
    # CTC structural gate, corrected: the library gives single-frame (one-letter) words a zero-length span, so
    # only words of >=2 letters must have non-zero duration; order must be monotonic.
    ok = []
    for w in ct:
        mono = all(w[k]["s"] <= w[k + 1]["s"] + 1e-6 for k in range(len(w) - 1))
        ok.append(bool(w) and mono and all((x["e"] - x["s"]) > 0 for x in w if len(norm_words(x["w"]) and norm_words(x["w"])[0]) >= 2))
    out["ctc_structural_gate_pass_rate_corrected"] = float(np.mean(ok))
    out["ctc_zero_len_words_total"] = int(sum(1 for w in ct for x in w if x["e"] - x["s"] <= 0))
    out["ctc_words_total"] = int(sum(len(w) for w in ct))
    if "piper_native" in meta:
        pn = meta["piper_native"]
        out["piper_native"] = {
            "sum_equals_audio_exact_rate": float(np.mean([p["aligned_samples"] == p["audio_samples"] for p in pn])),
            "all_chunks_aligned_rate": float(np.mean([p["all_chunks_aligned"] for p in pn])),
            "max_abs_sample_diff": int(max(abs(p["aligned_samples"] - p["audio_samples"]) for p in pn)),
            "word_count_match_rate": out.get("native_word_count_match_rate"),
        }
    (d / "compare.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path(__file__).parent / "output" / "spike_chapter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("text"); p.add_argument("--db", default=DEFAULT_DB); p.add_argument("--book", default=DEFAULT_BOOK)
    p.add_argument("--start", type=int, default=1500); p.add_argument("-n", type=int, default=520)
    p = sub.add_parser("synth"); p.add_argument("engine", choices=["kokoro", "piper"]); p.add_argument("--voice")
    p = sub.add_parser("drift"); p.add_argument("engine", choices=["kokoro", "piper"])
    p = sub.add_parser("align"); p.add_argument("engine", choices=["kokoro", "piper"]); p.add_argument("aligner", choices=["fw", "ctc"])
    p.add_argument("--threads", type=int, default=8); p.add_argument("--limit", type=int, default=0)
    p.add_argument("--lib-windows", action="store_true", help="ctc: use the library's 30 s window emissions instead of tight per-sentence input")
    p = sub.add_parser("piperbench"); p.add_argument("--reps", type=int, default=3); p.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("compare"); p.add_argument("engine", choices=["kokoro", "piper"])
    a = ap.parse_args()
    {"text": cmd_text, "synth": cmd_synth, "drift": cmd_drift, "align": cmd_align, "compare": cmd_compare, "piperbench": cmd_piperbench}[a.cmd](a)


if __name__ == "__main__":
    main()
