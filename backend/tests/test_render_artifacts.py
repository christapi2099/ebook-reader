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
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5, 2.0, 3.0), tmp_path)
    assert set(tiers) == {"1.0", "1.5", "2.0", "3.0"}
    for k in tiers:
        assert (tmp_path / f"audio_{k}.opus").is_file()
    dur = ra.ffprobe_duration_ms(tmp_path / "audio_1.0.opus")
    assert abs(base["segments"][-1]["end_ms"] - dur) < 50
    for k, t in tiers.items():
        assert abs(t["actual_factor"] - t["requested_factor"]) / t["requested_factor"] < 0.01
        last = t["segments"][-1]["end_ms"]
        assert abs(last - ra.ffprobe_duration_ms(tmp_path / f"audio_{k}.opus")) < 50
    before = set(tmp_path.iterdir())
    report = ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)
    assert set(report) == set(tiers)
    assert all(v <= 60 for v in report.values())
    assert set(tmp_path.iterdir()) == before  # reads the published files, writes nothing
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
    with pytest.raises(ra.RenderAuditError, match="actual_factor"):
        ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


def test_drift_audit_measures_where_the_audio_really_is(need_ffmpeg, tmp_path):
    """A claimed timeline 0.9% off passes the factor sanity check but puts the late
    boundaries ~55 ms away from the audio: the content match must catch it."""
    base = ra.build_base(_chunks(), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    tiers["1.5"]["actual_factor"] *= 1.009
    with pytest.raises(ra.RenderAuditError, match="drift"):
        ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


@pytest.fixture
def published(need_ffmpeg, tmp_path):
    base = ra.build_base(_chunks(), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5, 2.0), tmp_path)
    return base, tiers


def _audit(published, tmp_path):
    base, tiers = published
    return ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


def test_audit_passes_a_correct_render_at_every_boundary(published, tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "SAMPLE_EVERY", 1)
    report = _audit(published, tmp_path)
    assert report["1.0"] < 2.0          # the base tier is the base: no drift at all
    assert all(v <= ra.TOL_SAMPLED_MS for v in report.values())


def test_audit_fails_a_truncated_tier(published, tmp_path):
    p = tmp_path / "audio_1.5.opus"
    data = p.read_bytes()
    p.write_bytes(data[:len(data) * 2 // 3])
    with pytest.raises(ra.RenderAuditError, match="tier 1.5"):
        _audit(published, tmp_path)


def test_audit_fails_a_corrupt_tier(published, tmp_path):
    p = tmp_path / "audio_2.0.opus"
    data = bytearray(p.read_bytes())
    data[len(data) // 2:len(data) // 2 + 64] = bytes(64)
    p.write_bytes(bytes(data))
    with pytest.raises(ra.RenderAuditError, match="tier 2.0.*(corrupt|CRC)"):
        _audit(published, tmp_path)


def test_audit_fails_an_unreadable_tier(published, tmp_path):
    (tmp_path / "audio_2.0.opus").write_bytes(b"\0" * 4096)
    with pytest.raises(ra.RenderAuditError, match="tier 2.0"):
        _audit(published, tmp_path)


def test_audit_fails_a_swapped_tier(published, tmp_path):
    import shutil
    shutil.copy(tmp_path / "audio_2.0.opus", tmp_path / "audio_1.5.opus")
    with pytest.raises(ra.RenderAuditError, match="tier 1.5.*wrong tier"):
        _audit(published, tmp_path)


def test_audit_fails_other_audio_of_the_right_length(published, tmp_path):
    """Right duration, wrong content (another tier's audio stretched to fit): the
    content match must reject it even though every length check passes."""
    import subprocess
    base, tiers = published
    src = tmp_path / "audio_2.0.opus"
    reversed_ = tmp_path / "rev.opus"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp_path / "audio_1.5.opus"),
                    "-af", "areverse", "-c:a", "libopus", "-b:a", "24k", str(reversed_)], check=True)
    reversed_.replace(tmp_path / "audio_1.5.opus")
    with pytest.raises(ra.RenderAuditError, match="tier 1.5"):
        _audit(published, tmp_path)


def test_audit_refuses_a_silent_render(need_ffmpeg, tmp_path):
    chunks = [{**c, "audio": np.zeros_like(c["audio"])} for c in _chunks()]
    base = ra.build_base(chunks, tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    with pytest.raises(ra.RenderAuditError, match="no audible content"):
        ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


def test_a_flat_boundary_is_replaced_by_an_audible_neighbour(need_ffmpeg, tmp_path, monkeypatch):
    chunks = _chunks()
    for i in (0, 1):  # silence around boundary 0 (end of sentence 0)
        chunks[i] = {**chunks[i], "audio": np.zeros_like(chunks[i]["audio"])}
    base = ra.build_base(chunks, tmp_path)
    import soundfile as sf
    centers = np.array(ra._boundaries(base["segments"]))
    with sf.SoundFile(base["path"]) as fb:
        points, flat = ra._audit_points(fb, centers)
    assert 0 not in points and flat == 0 and len(points) >= 4


def test_edges_are_judged_on_the_median_of_three(monkeypatch, tmp_path):
    """One stray edge measurement passes (it is within the sampled tolerance); a
    real offset of the first three boundaries fails at the edge tolerance."""
    import soundfile as sf
    path = tmp_path / "t.flac"
    sf.write(path, np.zeros(24000 * 20, np.float32), 24000)
    centers = np.arange(1, 11) * 1000.0
    points = {i: None for i in range(10)}
    info = {"requested_factor": 1.0, "actual_factor": 1.0}
    monkeypatch.setattr(ra.ogg_opus, "check_stream", lambda p: {})

    def run(drifts):
        monkeypatch.setattr(ra, "_content_drift",
                            lambda env, ft, c, f, claimed: (drifts[int(c / 1000) - 1], 0.99))
        return ra._audit_tier("1.0", info, path, 20000.0, centers, points)

    assert run([45, 2, 3, 0, 0, 0, 0, 1, 2, 44]) == 45
    with pytest.raises(ra.RenderAuditError, match="median of the first 3"):
        run([40, 35, 3, 0, 0, 0, 0, 0, 0, 0])
    with pytest.raises(ra.RenderAuditError, match="median of the last 3"):
        run([0, 0, 0, 0, 0, 0, 0, -31, -32, 0])
    with pytest.raises(ra.RenderAuditError, match="exceeds 60"):
        run([0, 0, 0, 0, 61, 0, 0, 0, 0, 0])


def test_timing_quality():
    segs = [{"words": [{"timing_source": "kokoro"}, {"timing_source": "aligned"}]}]
    assert ra._timing_quality(segs) == "aligned"
    segs[0]["words"].append({"timing_source": "estimated"})
    assert ra._timing_quality(segs) == "estimated"


def test_encode_stages_report_increasing_progress(tmp_path):
    """The UI bar moves during encode and audit, so each stage reports 0..1 in order."""
    from services import render_artifacts as ra
    steps = {"base": [], "tiers": [], "audit": []}
    chunks = _chunks(n=4, chapter_at=9)
    base = ra.build_base(chunks, tmp_path, on_step=steps["base"].append)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path,
                           on_step=steps["tiers"].append)
    ra.drift_audit(base["path"], base["segments"], tiers, tmp_path, on_step=steps["audit"].append)
    for name, values in steps.items():
        assert values and values[-1] == 1.0, name
        assert values == sorted(values), name


def test_audit_reads_seeked_windows_not_the_whole_book(tmp_path):
    """Memory stays flat: windows are read by seeking, zero-padded past the ends."""
    import soundfile as sf
    y = np.random.default_rng(0).standard_normal(24000 * 30).astype(np.float32) * 0.1
    path = tmp_path / "x.flac"
    sf.write(path, y, 24000, format="FLAC", subtype="PCM_24")
    full, _ = sf.read(path, dtype="float32")
    with sf.SoundFile(path) as f:
        w = ra._read_window(f, 24000 * 10, 24000 * 11)
        assert np.array_equal(w, full[24000 * 10:24000 * 11])
        w = ra._read_window(f, -100, 200)
        assert np.array_equal(w[:100], np.zeros(100)) and np.array_equal(w[100:], full[:200])
        w = ra._read_window(f, len(full) - 50, len(full) + 50)
        assert np.array_equal(w[:50], full[-50:]) and not w[50:].any()
    reads = []
    real_read = sf.SoundFile.read

    def spy(self, frames=-1, *a, **k):
        reads.append(frames)
        return real_read(self, frames, *a, **k)

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(sf.SoundFile, "read", spy)
    try:
        with sf.SoundFile(path) as f:
            ra._envelope(f, 10_000, 11_000)
    finally:
        mp.undo()
    assert reads and max(reads) <= 24000 * 1.1 and -1 not in reads


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


@pytest.mark.parametrize("n", [1, 2])
@pytest.mark.parametrize("factor", [2.0, 2.5])
def test_very_short_books_pass_the_audit(need_ffmpeg, tmp_path, n, factor):
    """atempo drops 15-33 ms at the end of a file, so a 1-2 sentence book's
    actual_factor is >1% off requested (2.0 -> ~2.024) although nothing is wrong:
    a short pasted text must still become readable."""
    base = ra.build_base(_chunks(n=n, chapter_at=9), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (factor,), tmp_path)
    report = ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)
    assert set(report) == {"1.0", str(factor)}


def test_a_wrong_factor_on_a_short_book_still_fails(need_ffmpeg, tmp_path):
    """The relaxed factor check still catches a tier whose claimed factor puts its
    timeline more than TOL_DURATION_MS away."""
    base = ra.build_base(_chunks(n=2, chapter_at=9), tmp_path)
    tiers = ra.build_tiers(base["path"], base["segments"], (2.0,), tmp_path)
    tiers["2.0"]["actual_factor"] = 1.5
    with pytest.raises(ra.RenderAuditError, match="actual_factor"):
        ra.drift_audit(base["path"], base["segments"], tiers, tmp_path)


def test_finished_tier_survives_a_restart_and_only_for_the_same_base(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ra, "_encode", lambda src, dst, f: (calls.append(f), dst.write_bytes(b"x")))
    path = tmp_path / "audio_1.5.opus"
    ra._encode_tier(tmp_path / "base.flac", path, 1.5, 1000.0)
    ra._encode_tier(tmp_path / "base.flac", path, 1.5, 1000.0)  # restart: reused
    assert calls == [1.5]
    ra._encode_tier(tmp_path / "base.flac", path, 1.5, 1000.5)   # different base: redone
    assert calls == [1.5, 1.5]
    path.with_name(path.name + ".done").unlink()                  # no marker: never trusted
    ra._encode_tier(tmp_path / "base.flac", path, 1.5, 1000.5)
    assert calls == [1.5, 1.5, 1.5]
