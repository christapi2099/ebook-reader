"""Chunked parallel tier encode: same stream layout and timeline as one ffmpeg per
tier, equally good audio, splits in digital silence, clean failures, steady progress."""
import io
import subprocess
import threading

import numpy as np
import pytest
import soundfile as sf

from services import export_encoding, ogg_opus as oo, render_artifacts as ra, tier_encode as te

SR = 24000
TIERS = (1.5, 2.0, 3.0)


@pytest.fixture
def need_ffmpeg(real_ffmpeg):
    assert export_encoding.ffmpeg_available(), "ffmpeg required"


def speech_like(rng, seconds):
    """Voiced harmonics with a wandering pitch and 4-6 Hz syllable bumps, plus noise
    bursts: the envelope and spectrum move the way speech does, never exactly zero."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f0 = rng.uniform(110, 200) * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(0.5, 2) * t))
    ph = 2 * np.pi * np.cumsum(f0) / SR
    voiced = sum(np.sin(h * ph) / h for h in range(1, 12))
    rate = rng.uniform(4, 6)
    syll = 0.55 + 0.45 * np.sin(2 * np.pi * rate * t + rng.uniform(0, 6))
    noise = np.convolve(rng.standard_normal(n), np.ones(4) / 4, mode="same")
    burst = (np.sin(2 * np.pi * rate / 2 * t) > 0.8).astype(float)
    a = 0.12 * voiced * syll ** 2 + 0.05 * noise * burst
    fade = np.minimum(1, np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.01 * SR))
    return (a * fade + 1e-3).astype(np.float32)   # offset: no exact zeros inside a sentence


def book_chunks(n=34, seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        a = speech_like(rng, rng.uniform(0.8, 1.6))
        dur_ms = len(a) / SR * 1000
        words = [{"word": f"w{k}", "start_ms": k * dur_ms / 3, "end_ms": (k + 1) * dur_ms / 3 - 5,
                  "box": None, "timing_source": "kokoro"} for k in range(3)]
        out.append({"index": i, "audio": a, "words": words, "chapter": 0 if i < n // 2 else 1,
                    "text": f"sentence {i}."})
    return out


def decode(path):
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "s16le",
                        "-ar", str(SR), "-ac", "1", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, "<i2").astype(np.float64) / 32768


def source_pcm(base, factor):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(base)]
    if factor != 1.0:
        cmd += ["-filter:a", ra.atempo_filter_chain(factor)]
    cmd += ["-ar", str(SR), "-ac", "1", "-f", "s16le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(r.stdout, "<i2").astype(np.float64) / 32768


@pytest.fixture(scope="module")
def encodes(tmp_path_factory):
    """One book encoded both ways: monolithic (1 worker) and chunked (4-s chunks)."""
    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("enc")
    chunks = book_chunks()
    base = ra.build_base(chunks, root)
    out = {}
    try:
        for name, workers in (("mono", "1"), ("chunked", "4")):
            d = root / name
            d.mkdir()
            mp.setenv("RENDER_ENCODE_WORKERS", workers)
            mp.setattr(te, "CHUNK_S", 4.0)
            steps, threads = [], set()

            def on_step(f):
                steps.append(f)
                threads.add(threading.get_ident())

            mp.setattr(te, "PROGRESS_EVERY_S", 0.0)
            res = ra.build_tiers(base["path"], base["segments"], TIERS, d, on_step=on_step)
            out[name] = {"dir": d, "res": res, "steps": steps, "threads": threads}
    finally:
        mp.undo()
    out["base"] = base
    return out


def _real_ffmpeg_present():
    import shutil
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(not _real_ffmpeg_present(), reason="ffmpeg required")


def test_chunked_stream_matches_monolithic_layout(encodes):
    mono, ch = encodes["mono"], encodes["chunked"]
    assert list(ch["res"]) == list(mono["res"]) == ["1.0", "1.5", "2.0", "3.0"]
    for k in mono["res"]:
        pm = oo.read_packets((mono["dir"] / f"audio_{k}.opus").read_bytes())
        pc = oo.read_packets((ch["dir"] / f"audio_{k}.opus").read_bytes())
        assert len(pc[1]) == len(pm[1]), k                  # packet count
        assert pc[2] == pm[2], k                            # final granule
        assert pc[1][0] == pm[1][0] and pc[1][1] == pm[1][1], k   # OpusHead, OpusTags
        info_c = oo.check_stream(ch["dir"] / f"audio_{k}.opus")
        info_m = oo.check_stream(mono["dir"] / f"audio_{k}.opus")
        assert info_c["pages"] == info_m["pages"], k
        assert ra.ffprobe_duration_ms(ch["dir"] / f"audio_{k}.opus") == pytest.approx(
            ra.ffprobe_duration_ms(mono["dir"] / f"audio_{k}.opus"), abs=1e-6), k
        assert ch["res"][k]["actual_factor"] == mono["res"][k]["actual_factor"], k
        assert ch["res"][k]["duration_ms"] == mono["res"][k]["duration_ms"], k
        # Up to the first split both encoders saw the same samples from the same
        # state: the first >= 2 s (half a chunk) of packets are bit-identical.
        first_diff = next((i for i in range(2, len(pm[1])) if pc[1][i] != pm[1][i]), len(pm[1]))
        assert first_diff - 2 >= 2.0 * SR / te.FRAME, k


def test_chunked_decodes_to_the_same_audio_as_monolithic(encodes):
    """Same decoded length; the error against the encoder input (the atempo PCM) is
    as small as the monolithic encode's overall, and about the same in each 1-s window.

    Sample-exact equality is impossible after the first split (the VBR state
    differs, so different but equally good packets come out); before it the
    packets are bit-identical (see the layout test)."""
    base = encodes["base"]["path"]
    for k, info in encodes["mono"]["res"].items():
        src = source_pcm(base, info["requested_factor"])
        xm = decode(encodes["mono"]["dir"] / f"audio_{k}.opus")
        xc = decode(encodes["chunked"]["dir"] / f"audio_{k}.opus")
        assert len(xc) == len(xm) == len(src), k
        em, ec = xm - src, xc - src
        snr = lambda e: 10 * np.log10(np.sum(src ** 2) / np.sum(e ** 2))
        assert abs(snr(ec) - snr(em)) < 0.5, (k, snr(ec), snr(em))
        w = SR
        nwin = len(src) // w
        pw = np.sum(src[:nwin * w].reshape(nwin, w) ** 2, axis=1)
        cw = np.sum(ec[:nwin * w].reshape(nwin, w) ** 2, axis=1)
        mw = np.sum(em[:nwin * w].reshape(nwin, w) ** 2, axis=1)
        voiced = pw > 1e-3 * pw.max()
        worse_db = 10 * np.log10(cw[voiced] / mw[voiced])
        # Two valid encodes differ window to window (different VBR state after a
        # split): measured here within -2..+3.2 dB, median 0, so allow a little more.
        assert abs(np.median(worse_db)) < 0.5, (k, worse_db)
        assert np.percentile(worse_db, 95) < 2.0, (k, worse_db)
        assert worse_db.max() < 4.5, (k, worse_db)
        # and the decoded loudness envelope (10 ms) tracks the monolithic one
        h = SR // 100
        nh = len(xm) // h
        env = lambda x: 10 * np.log10(np.mean(x[:nh * h].reshape(nh, h) ** 2, axis=1) + 1e-8)
        loud = env(xm) > env(xm).max() - 30
        assert np.median(np.abs(env(xc) - env(xm))[loud]) < 0.5, k


def test_splices_fall_in_digital_silence(encodes):
    """Around each split the source is exact zeros, so both decodes are silent there."""
    base = encodes["base"]["path"]
    for k, info in encodes["mono"]["res"].items():
        src = (source_pcm(base, info["requested_factor"]) * 32768).round().astype("<i2")
        xc = decode(encodes["chunked"]["dir"] / f"audio_{k}.opus")
        chunk = int(4.0 * SR) // te.FRAME * te.FRAME
        a, splits = 0, []
        while a + chunk + chunk // 2 + te.TAIL + te.SPLIT_MARGIN <= len(src):
            b = te.find_split(src, 0, a + chunk // 2, a + chunk + chunk // 2, a + chunk)
            assert b is not None, k   # every chunk of this book has a sentence gap
            splits.append(b)
            a = b
        assert splits, k
        for b in splits:
            p = te.splice_instant(b)
            assert b % te.FRAME == 0
            assert not src[p - te.SPLIT_MARGIN:p + te.SPLIT_MARGIN].any()
            assert np.abs(xc[p - 120:p + 120]).max() < 0.01, (k, b)


def test_progress_is_per_chunk_from_the_calling_thread(encodes):
    ch = encodes["chunked"]
    steps = ch["steps"]
    assert ch["threads"] == {threading.get_ident()}
    assert steps == sorted(steps) and steps[0] == 0.0 and steps[-1] == 1.0
    # one call per spliced chunk (PROGRESS_EVERY_S = 0) plus start and end
    assert len(steps) >= 10


# ------------------------------------------------------------ split points
def test_find_split_picks_the_silence_centre_nearest_the_target():
    x = np.ones(SR * 10, dtype="<i2")
    x[2 * SR:2 * SR + 4800] = 0          # 200 ms of silence at 2.0 s
    x[6 * SR:6 * SR + 2400] = 0          # 100 ms at 6.0 s
    b = te.find_split(x, 0, SR, 9 * SR, 5 * SR)
    p = te.splice_instant(b)
    assert b % te.FRAME == 0 and 6 * SR + te.SPLIT_MARGIN <= p <= 6 * SR + 2400 - te.SPLIT_MARGIN
    assert abs(p - (6 * SR + 1200)) <= te.FRAME // 2      # as central as the grid allows
    b = te.find_split(x, 0, SR, 9 * SR, 2 * SR)
    p = te.splice_instant(b)
    assert 2 * SR + te.SPLIT_MARGIN <= p <= 2 * SR + 4800 - te.SPLIT_MARGIN


def test_find_split_needs_enough_zeros_and_respects_the_window():
    x = np.ones(SR * 10, dtype="<i2")
    x[3 * SR:3 * SR + 2 * te.SPLIT_MARGIN - 1] = 0   # one sample too short
    assert te.find_split(x, 0, 0, 10 * SR, 3 * SR) is None
    x[5 * SR + 7:5 * SR + 7 + 2 * te.SPLIT_MARGIN] = 0   # long enough, but no grid point fits
    assert te.find_split(x, 0, 0, 6 * SR, 3 * SR) is None
    x[7 * SR:7 * SR + 4800] = 0
    assert te.find_split(x, 0, 0, 6 * SR, 3 * SR) is None       # outside [lo, hi]
    # global offsets: the same buffer seen as starting at sample 1000 * FRAME
    off = 1000 * te.FRAME
    b = te.find_split(x, off, off, off + 10 * SR, off + 7 * SR)
    p = te.splice_instant(b) - off
    assert b is not None and b % te.FRAME == 0 and not x[p - te.SPLIT_MARGIN:p + te.SPLIT_MARGIN].any()


def test_no_silence_still_splits_on_the_grid(need_ffmpeg, tmp_path):
    """A stretch with no digital silence is split on the frame grid anyway (memory
    stays bounded); the stream is still complete and correctly timed."""
    rng = np.random.default_rng(5)
    base = tmp_path / "noise.flac"
    sf.write(base, (0.1 * rng.standard_normal(SR * 11)).astype(np.float32), SR,
             format="FLAC", subtype="PCM_16")
    out = te.encode_tiers(base, [("1.0", None, tmp_path / "c.opus")], base_samples=SR * 11,
                          factors={"1.0": 1.0}, workers=3, bitrate_k=24, chunk_s=2.0)
    assert out["1.0"]["forced_splits"] >= 3
    ra._encode(base, tmp_path / "m.opus", 1.0)
    pc = oo.read_packets((tmp_path / "c.opus").read_bytes())
    pm = oo.read_packets((tmp_path / "m.opus").read_bytes())
    assert len(pc[1]) == len(pm[1]) and pc[2] == pm[2]
    assert len(decode(tmp_path / "c.opus")) == SR * 11


# ------------------------------------------------------------ path choice
def test_short_books_and_books_without_gaps_use_one_ffmpeg_per_tier(monkeypatch):
    monkeypatch.setenv("RENDER_ENCODE_WORKERS", "8")
    segs = [{"gap_ms": 120}, {"gap_ms": 0}]
    assert ra._use_chunked(te.CHUNK_S * 1000 * 1.5, segs) == 8
    assert ra._use_chunked(te.CHUNK_S * 1000 * 1.5 - 1, segs) == 0
    assert ra._use_chunked(3_600_000, [{"gap_ms": 0}]) == 0
    monkeypatch.setenv("RENDER_ENCODE_WORKERS", "1")
    assert ra._use_chunked(3_600_000, segs) == 0


def test_encode_workers_env(monkeypatch):
    monkeypatch.delenv("RENDER_ENCODE_WORKERS", raising=False)
    monkeypatch.delattr(te.os, "sched_getaffinity", raising=False)
    for cpus, want in ((112, 48), (16, 8), (3, 1), (1, 1), (None, 1)):
        monkeypatch.setattr(te.os, "cpu_count", lambda c=cpus: c)
        assert te.encode_workers() == want, cpus
    monkeypatch.setattr(te.os, "cpu_count", lambda: 16)
    for raw, want in (("12", 12), ("1", 1), ("0", 8), ("x", 8)):
        monkeypatch.setenv("RENDER_ENCODE_WORKERS", raw)
        assert te.encode_workers() == want, raw


# ------------------------------------------------------------ failures
def _chunked_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER_ENCODE_WORKERS", "3")
    monkeypatch.setattr(te, "CHUNK_S", 3.0)
    base = ra.build_base(book_chunks(n=16, seed=9), tmp_path)
    return base


def _leftovers(d):
    return sorted(p.name for p in d.iterdir() if p.name.startswith("audio_"))


def test_a_failed_chunk_raises_render_error_and_leaves_nothing(need_ffmpeg, tmp_path, monkeypatch):
    base = _chunked_setup(monkeypatch, tmp_path)
    real = te._encode_chunk

    def flaky(job, bitrate_k):
        if job.tier.key == "2.0" and job.k == 1:
            raise te.ChunkEncodeError("tier 2.0 chunk 1: boom")
        return real(job, bitrate_k)

    monkeypatch.setattr(te, "_encode_chunk", flaky)
    before = threading.active_count()
    with pytest.raises(ra.RenderError, match="boom") as ei:
        ra.build_tiers(base["path"], base["segments"], TIERS, tmp_path)
    assert ei.value.phase == "encode"
    assert _leftovers(tmp_path) == []
    assert threading.active_count() == before


def test_garbage_from_an_encoder_is_a_render_error(need_ffmpeg, tmp_path, monkeypatch):
    base = _chunked_setup(monkeypatch, tmp_path)

    def garbage(job, bitrate_k):
        oo.read_packets(b"OggS but not really")
        raise AssertionError("unreachable")

    monkeypatch.setattr(te, "_encode_chunk", garbage)
    with pytest.raises(ra.RenderError, match="chunked tier encode failed") as ei:
        ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    assert ei.value.phase == "encode"
    assert _leftovers(tmp_path) == []


def test_a_failing_decoder_is_reported(need_ffmpeg, tmp_path, monkeypatch):
    base = _chunked_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ra, "atempo_filter_chain", lambda f: "atempo=nonsense")
    with pytest.raises(ra.RenderError, match="ffmpeg decode failed") as ei:
        ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    assert ei.value.phase == "encode"
    assert _leftovers(tmp_path) == []


def test_an_abort_from_on_step_stops_everything(need_ffmpeg, tmp_path, monkeypatch):
    """on_step raising (the worker's supersede/stall stop) must stop the producers
    and encoders and remove the half-written tiers."""
    base = _chunked_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(te, "PROGRESS_EVERY_S", 0.0)
    calls = []

    class Stop(Exception):
        pass

    def on_step(f):
        calls.append(f)
        if len(calls) == 3:
            raise Stop()

    before = threading.active_count()
    with pytest.raises(Stop):
        ra.build_tiers(base["path"], base["segments"], TIERS, tmp_path, on_step=on_step)
    assert _leftovers(tmp_path) == []
    assert threading.active_count() == before


def test_stale_part_files_are_removed(need_ffmpeg, tmp_path, monkeypatch):
    base = _chunked_setup(monkeypatch, tmp_path)
    (tmp_path / "audio_1.25.opus.part").write_bytes(b"half")
    ra.build_tiers(base["path"], base["segments"], (2.0,), tmp_path)
    assert _leftovers(tmp_path) == ["audio_1.0.opus", "audio_2.0.opus"]


# ------------------------------------------------------------ Ogg container
def test_writer_reproduces_ffmpegs_own_file(need_ffmpeg, tmp_path):
    rng = np.random.default_rng(2)
    src = tmp_path / "x.flac"
    sf.write(src, speech_like(rng, 7.3), SR, format="FLAC", subtype="PCM_16")
    ra._encode(src, tmp_path / "x.opus", 1.0)
    buf = (tmp_path / "x.opus").read_bytes()
    serial, packets, granule = oo.read_packets(buf)
    out = io.BytesIO()
    w = oo.OggOpusWriter(out, serial, packets[0], packets[1])
    w.write(packets[2:])
    assert w.close(granule) == len(packets) - 2
    assert out.getvalue() == buf


def test_check_stream_catches_damage(need_ffmpeg, tmp_path):
    rng = np.random.default_rng(4)
    src = tmp_path / "x.flac"
    sf.write(src, speech_like(rng, 5.0), SR, format="FLAC", subtype="PCM_16")
    ra._encode(src, tmp_path / "x.opus", 1.0)
    good = (tmp_path / "x.opus").read_bytes()
    info = oo.check_stream(tmp_path / "x.opus")
    assert info["pre_skip"] == 312 and info["granule"] == 312 + 2 * 5 * SR
    bad = tmp_path / "bad.opus"
    flipped = bytearray(good)
    flipped[len(good) // 2] ^= 0xFF
    for name, data in (("crc", bytes(flipped)), ("truncated", good[:len(good) * 2 // 3]),
                       ("garbage", b"not an ogg file at all" * 10), ("empty", b"")):
        bad.write_bytes(data)
        with pytest.raises(oo.OggError):
            oo.check_stream(bad)


# ------------------------------------------------------------ review follow-ups
def test_a_hung_chunk_encoder_times_out(need_ffmpeg, tmp_path, monkeypatch):
    base = _chunked_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(te, "CHUNK_TIMEOUT_S", 0.001)
    with pytest.raises(ra.RenderError, match="timed out") as ei:
        ra.build_tiers(base["path"], base["segments"], (1.5,), tmp_path)
    assert ei.value.phase == "encode"
    assert _leftovers(tmp_path) == []


def test_finished_tiers_are_removed_when_a_later_one_fails(need_ffmpeg, tmp_path, monkeypatch):
    """A failure after another tier was already renamed into place removes it too."""
    import time
    base = _chunked_setup(monkeypatch, tmp_path)
    real = te._encode_chunk

    def late_failure(job, bitrate_k):
        if job.tier.key == "3.0" and job.last:
            deadline = time.monotonic() + 20
            while not (tmp_path / "audio_1.0.opus").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "audio_1.0.opus").exists()
            raise te.ChunkEncodeError("tier 3.0: late boom")
        return real(job, bitrate_k)

    monkeypatch.setattr(te, "_encode_chunk", late_failure)
    with pytest.raises(ra.RenderError, match="late boom"):
        ra.build_tiers(base["path"], base["segments"], TIERS, tmp_path)
    assert _leftovers(tmp_path) == []


def test_the_final_granule_self_check_rejects_a_wrong_splice(tmp_path):
    t = te._Tier("1.5", None, tmp_path / "x.opus", 0, 100)
    t.head = b"OpusHead" + bytes([1, 1]) + (312).to_bytes(2, "little") + bytes(7)
    t.total = 24000                                   # 1 s -> granule 312 + 48000
    t._check(51, 312 + 48000)                         # consistent: no error
    with pytest.raises(te.ChunkEncodeError, match="inconsistent"):
        t._check(51, 312 + 48000 - 960)               # a packet's worth short
    with pytest.raises(te.ChunkEncodeError, match="inconsistent"):
        t._check(40, 312 + 48000)                     # too few packets for that granule


def test_encode_workers_respects_cpu_affinity(monkeypatch):
    monkeypatch.delenv("RENDER_ENCODE_WORKERS", raising=False)
    monkeypatch.setattr(te.os, "cpu_count", lambda: 112)
    monkeypatch.setattr(te.os, "sched_getaffinity", lambda pid: set(range(10)), raising=False)
    assert te.encode_workers() == 5
    monkeypatch.delattr(te.os, "sched_getaffinity", raising=False)
    assert te.encode_workers() == 48
