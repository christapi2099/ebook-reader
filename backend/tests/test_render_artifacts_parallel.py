"""Parallel tier encoding: N workers must equal serial, in order, with clean aborts."""
import json
import random
import subprocess
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from services import export_encoding, render_artifacts as ra
from tests.test_render_artifacts import _chunks

TIERS = (0.75, 1.25, 1.5, 2.0)


@pytest.fixture
def need_ffmpeg(real_ffmpeg):
    assert export_encoding.ffmpeg_available(), "ffmpeg required"


def _pcm(path):
    wav = path.with_suffix(".dec.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-ar", "24000", "-ac", "1",
                    str(wav)], check=True)
    y, _ = sf.read(wav, dtype="float32")
    wav.unlink()
    return y


def _full(tmp_path, workers, monkeypatch):
    monkeypatch.setenv("RENDER_TIER_WORKERS", str(workers))
    d = tmp_path / f"w{workers}"
    d.mkdir()
    base = ra.build_base(_chunks(), d)
    tiers = ra.build_tiers(base["path"], base["segments"], TIERS, d)
    ra.drift_audit(base["path"], base["segments"], tiers, d)
    m = ra.write_manifest(d, base_segments=base["segments"], tier_results=tiers,
                          duration_ms=base["duration_ms"], voice="v", voice_file_sha256="x",
                          fingerprint="f")
    return d, tiers, m


def test_workers_1_vs_4_equivalent(need_ffmpeg, tmp_path, monkeypatch):
    d1, t1, m1 = _full(tmp_path, 1, monkeypatch)
    d4, t4, m4 = _full(tmp_path, 4, monkeypatch)
    assert list(t1) == list(t4) == ["1.0", "0.75", "1.25", "1.5", "2.0"]
    assert list(m1["tiers"]) == list(m4["tiers"])
    assert json.dumps(m1, sort_keys=True) == json.dumps(m4, sort_keys=True)
    for k in t1:
        assert t1[k]["actual_factor"] == t4[k]["actual_factor"]
        assert t1[k]["max_drift_ms"] == t4[k]["max_drift_ms"]
        a, b = _pcm(d1 / f"audio_{k}.opus"), _pcm(d4 / f"audio_{k}.opus")
        assert a.shape == b.shape and np.allclose(a, b, atol=1e-3), k


def _fake_env(tmp_path, monkeypatch, workers, fail_on=None, delays=True):
    monkeypatch.setenv("RENDER_TIER_WORKERS", str(workers))
    rnd = random.Random(7)
    calls, live, peak = [], [0], [0]
    lock = threading.Lock()

    def fake_encode(src, dst, factor):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            calls.append(factor)
        try:
            if delays:
                time.sleep(rnd.random() * 0.05)
            dst.write_bytes(b"x")
            if factor == fail_on:
                raise ra.RenderError(f"boom {factor}", phase="encode")
        finally:
            with lock:
                live[0] -= 1

    monkeypatch.setattr(ra, "_encode", fake_encode)
    monkeypatch.setattr(ra, "ffprobe_duration_ms", lambda p: 1000.0)
    return calls, peak


SEGS = [{"index": 0, "start_ms": 0.0, "end_ms": 500.0, "gap_ms": 120,
         "words": [{"start_ms": 0.0, "end_ms": 100.0}]}]


def test_order_and_monotone_progress(tmp_path, monkeypatch):
    _, peak = _fake_env(tmp_path, monkeypatch, 4)
    steps, threads = [], set()

    def on_step(f):
        steps.append(f)
        threads.add(threading.get_ident())

    tiers = (0.5, 0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
    out = ra.build_tiers(tmp_path / "base.flac", SEGS, tiers, tmp_path, on_step=on_step)
    assert list(out) == ["1.0"] + [str(float(t)) for t in tiers]
    assert steps == sorted(steps) and steps[-1] == 1.0
    assert threads == {threading.get_ident()}
    assert 1 < peak[0] <= 4


def test_duplicate_tiers_do_not_share_files(tmp_path, monkeypatch):
    calls, _ = _fake_env(tmp_path, monkeypatch, 4)
    out = ra.build_tiers(tmp_path / "base.flac", SEGS, (1.5, 1.5, 2.0), tmp_path)
    assert list(out) == ["1.0", "1.5", "2.0"] and sorted(calls) == [1.0, 1.5, 2.0]


def test_failure_aborts_cleanly(tmp_path, monkeypatch):
    calls, _ = _fake_env(tmp_path, monkeypatch, 2, fail_on=1.25)
    before = threading.active_count()
    with pytest.raises(ra.RenderError, match="boom 1.25") as ei:
        ra.build_tiers(tmp_path / "base.flac", SEGS, (0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0), tmp_path)
    assert ei.value.phase == "encode"
    assert 3.0 not in calls  # pending tiers were never started
    assert not (tmp_path / "audio_1.25.opus").exists()
    assert threading.active_count() == before


def test_audit_failure_aborts(tmp_path, monkeypatch):
    _fake_env(tmp_path, monkeypatch, 4)
    base = tmp_path / "base.flac"
    sf.write(base, np.zeros(24000 * 3, np.float32), 24000)
    segs = [{"index": i, "start_ms": i * 1000.0, "end_ms": i * 1000.0 + 800, "gap_ms": 200,
             "words": []} for i in range(2)]
    results = {k: {"requested_factor": float(k), "actual_factor": float(k), "segments": []}
               for k in ("1.0", "1.5", "2.0")}
    monkeypatch.setattr(ra, "_run", lambda cmd: None)
    real_read = sf.read
    monkeypatch.setattr(sf, "read", lambda p, dtype=None: (np.zeros(24000 * 3, np.float32), 24000)
                        if str(p).endswith(".wav") else real_read(p, dtype=dtype))
    with pytest.raises(ra.RenderError) as ei:
        ra.drift_audit(base, segs, results, tmp_path)
    assert ei.value.phase == "drift_audit"
    assert not (tmp_path / "audit_markers.flac").exists()


def test_env_parsing(monkeypatch):
    monkeypatch.setattr(ra.os, "cpu_count", lambda: 16)
    monkeypatch.delenv("RENDER_TIER_WORKERS", raising=False)
    assert ra.tier_workers() == 4
    monkeypatch.setattr(ra.os, "cpu_count", lambda: 2)
    assert ra.tier_workers() == 1
    monkeypatch.setattr(ra.os, "cpu_count", lambda: 8)
    for raw, want in (("1", 1), ("3", 3), ("0", 4), ("-2", 4), ("abc", 4), ("", 4)):
        monkeypatch.setenv("RENDER_TIER_WORKERS", raw)
        assert ra.tier_workers() == want, raw
