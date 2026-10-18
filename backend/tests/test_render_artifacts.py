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
