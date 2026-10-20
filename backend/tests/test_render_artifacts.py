import json
from pathlib import Path

import numpy as np
import pytest

from services import export_encoding, render_artifacts as ra

SR = 24000


def _chunks(n=12, chapter_at=6):
    out = []
    for i in range(n):
        dur_ms = 700 + 40 * (i % 5)
        t = np.arange(int(SR * dur_ms / 1000)) / SR
        a = (0.2 * np.sin(2 * np.pi * (300 + 20 * i) * t)).astype(np.float32)
        words = [{"word": "w%d" % k, "start_ms": k * dur_ms / 3, "end_ms": (k + 1) * dur_ms / 3 - 5,
                  "box": [0, 0, 1, 1], "timing_source": "kokoro"} for k in range(3)]
        out.append({"index": i, "audio": a, "words": words, "chapter": 0 if i < chapter_at else 1,
                    "text": "sentence %d%s" % (i, "," if i == 2 else ".")})
    return out


@pytest.fixture
def need_ffmpeg(real_ffmpeg):
    assert export_encoding.ffmpeg_available(), "ffmpeg required"


def test_gap_policy_and_segments(tmp_path):
    ch = _chunks()
    base = ra.build_base(ch, tmp_path)
    segs = base["segments"]
    assert len(segs) == len(ch)
    for a, b in zip(segs, segs[1:]):
        assert b["start_ms"] >= a["end_ms"] + a["gap_ms"] - 0.1
    assert segs[2]["gap_ms"] == 0
    assert segs[5]["gap_ms"] == 250
    assert segs[0]["gap_ms"] == 120
    for s in segs:
        prev = None
        for w in s["words"]:
            assert s["start_ms"] <= w["start_ms"] <= w["end_ms"] <= s["end_ms"]
            assert prev is None or w["start_ms"] >= prev
            prev = w["start_ms"]
    assert (tmp_path / "chunks" / "0.flac").is_file()


def test_base_speed_guard(tmp_path):
    with pytest.raises(ra.RenderError) as e:
        ra.build_base(_chunks(), tmp_path, effective_speed=0.9)
    assert e.value.phase == "base_speed_mismatch"
    with pytest.raises(ra.RenderError):
        ra.write_manifest(tmp_path, base_segments=[], tier_results={}, duration_ms=0, voice="v",
                          voice_file_sha256="x", fingerprint="f", base_effective_speed=0.9)


def test_tiers_audit_manifest(need_ffmpeg, tmp_path):
    base = ra.build_base(_chunks(), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (0.75, 1.5, 2.0), tmp_path)
    assert set(tiers) == {"1.0", "0.75", "1.5", "2.0"}
    for k in tiers:
        assert (tmp_path / f"audio_{k}.opus").is_file()
    dur = ra.ffprobe_duration_ms(tmp_path / "audio_1.0.opus")
    assert abs(base["segments"][-1]["end_ms"] - dur) < 50
    for k, t in tiers.items():
        assert abs(t["actual_factor"] - t["requested_factor"]) / t["requested_factor"] < 0.01
        last = t["segments"][-1]["end_ms"]
        assert abs(last - ra.ffprobe_duration_ms(tmp_path / f"audio_{k}.opus")) < 50
    report = ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)
    assert set(report) == set(tiers)
    assert all(v <= 60 for v in report.values())
    m = ra.write_manifest(tmp_path, base_segments=base["segments"], tier_results=tiers,
                          duration_ms=base["duration_ms"], voice="af_heart", voice_file_sha256="ab",
                          fingerprint="fp")
    disk = json.loads((tmp_path / "manifest.json").read_text())
    assert disk["schema"] == 1 and disk["timing_quality"] == "exact" and disk["container"] == "ogg-opus"
    assert disk["base_effective_speed"] == 1.0 and set(disk["tiers"]) == set(tiers)
    assert len(disk["tiers"]["1.5"]["segments"]) == 12
    assert m["tiers"]["1.0"]["max_drift_ms"] <= 30


def test_drift_audit_fails_on_excess_drift(need_ffmpeg, tmp_path):
    base = ra.build_base(_chunks(), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    tiers["1.5"]["actual_factor"] *= 1.05  # wrong claimed factor -> linear prediction off
    with pytest.raises(ra.RenderAuditError):
        ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


def test_timing_quality():
    segs = [{"words": [{"timing_source": "kokoro"}, {"timing_source": "aligned"}]}]
    assert ra._timing_quality(segs) == "aligned"
    segs[0]["words"].append({"timing_source": "estimated"})
    assert ra._timing_quality(segs) == "estimated"


def test_encode_stages_report_increasing_progress(tmp_path):
    """The UI bar moves during encode and audit, so each stage reports 0..1 in order."""
    from services import render_artifacts as ra
    steps = {"base": [], "tiers": [], "audit": []}
    chunks = [{"index": i, "audio": np.zeros(2400, np.float32), "text": "t", "chapter": 0,
               "words": [{"word": "a", "start_ms": 0.0, "end_ms": 100.0, "box": None,
                          "timing_source": "estimated"}]} for i in range(3)]
    base = ra.build_base(chunks, tmp_path, on_step=steps["base"].append)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path,
                           on_step=steps["tiers"].append)
    ra.drift_audit(base["path"], base["segments"], tiers, tmp_path, on_step=steps["audit"].append)
    for name, values in steps.items():
        assert values and values[-1] == 1.0, name
        assert values == sorted(values), name


def _detect_whole_signal(y, expected_ms, half_ms=150.0):
    """The pre-windowing implementation, kept as the reference the windowed one must match."""
    t = np.arange(len(y)) / ra.SR
    z = y * np.exp(-2j * np.pi * ra.MARK_HZ * t)
    k = int(ra.SR * 0.004)
    env = np.abs(np.convolve(z, np.ones(k) / k, mode="same"))
    out = []
    for e in expected_ms:
        lo = int(max(0, e - half_ms) / 1000 * ra.SR)
        hi = int(min(len(y) / ra.SR * 1000, e + half_ms) / 1000 * ra.SR)
        w = env[lo:hi]
        pk = w.max()
        a0 = int(np.argmax(w))
        m = w > 0.5 * pk
        l = r = a0
        while l > 0 and m[l - 1]:
            l -= 1
        while r < len(w) - 1 and m[r + 1]:
            r += 1
        idx = np.arange(l, r + 1)
        out.append((lo + float((idx * w[idx]).sum() / w[idx].sum())) / ra.SR * 1000)
    return np.array(out)


def test_windowed_detect_matches_whole_signal_and_reads_files(tmp_path):
    import soundfile as sf
    rng = np.random.default_rng(0)
    sr, secs = ra.SR, 20
    y = (0.05 * rng.standard_normal(sr * secs)).astype(np.float32)
    n = int(sr * ra.MARK_MS / 1000)
    tone = (0.6 * np.hanning(n) * np.sin(2 * np.pi * ra.MARK_HZ * np.arange(n) / sr)).astype(np.float32)
    expected = np.array([300.0, 5000.0, 12345.0, 19800.0])  # near both ends and in between
    for e in expected:
        c = int(e / 1000 * sr)
        y[c - n // 2:c - n // 2 + n] += tone
    ref = _detect_whole_signal(y, expected)
    assert np.allclose(ra._detect(y, expected), ref, atol=1e-6)
    wav = tmp_path / "m.wav"
    sf.write(wav, y, sr, subtype="FLOAT")
    assert np.allclose(ra._detect_file(wav, expected), ref, atol=1e-6)
    assert np.allclose(ref, expected, atol=2.0)  # and it actually finds the markers


def test_build_base_streams_same_samples_as_concatenation(tmp_path):
    import soundfile as sf
    chunks = _chunks()
    base = ra.build_base(chunks, tmp_path)
    got, sr = sf.read(base["path"], dtype="float32")
    pieces = []
    for i, c in enumerate(chunks):
        pieces.append(np.asarray(c["audio"], dtype=np.float32))
        gap = ra.gap_after(c, chunks[i + 1] if i + 1 < len(chunks) else None)
        if gap:
            pieces.append(np.zeros(int(round(gap * ra.SR / 1000)), dtype=np.float32))
    want = np.concatenate(pieces)
    ref = tmp_path / "ref.flac"
    sf.write(ref, want, ra.SR, format="FLAC", subtype="PCM_16")
    expect, _ = sf.read(ref, dtype="float32")
    assert sr == ra.SR and len(got) == len(expect) and np.array_equal(got, expect)
    assert base["segments"][-1]["end_ms"] == pytest.approx(base["duration_ms"])
