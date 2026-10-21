"""WebM copies of the Ogg tiers: remux, the all-or-nothing format rule, and the routes."""
import subprocess

import pytest

from services import manifest_cache, webm_remux


def make_ogg(path, seconds=2.0):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
         "-ar", "24000", "-ac", "1", "-c:a", "libopus", "-b:a", "24k", str(path)], check=True)
    return path


def test_remux_is_a_stream_copy_with_the_same_length(tmp_path):
    ogg = make_ogg(tmp_path / "audio_1.0.opus")
    webm = webm_remux.remux_tier(ogg)
    assert webm == tmp_path / "audio_1.0.webm" and webm.stat().st_size > 0
    assert not list(tmp_path.glob("*.part"))
    codec = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(webm)],
                           capture_output=True, text=True).stdout.strip()
    assert codec == "opus"
    # same packets: the Ogg and WebM decode to identical PCM
    pcm = lambda p: subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(p), "-f", "s16le", "-"],
                                   capture_output=True).stdout
    assert pcm(ogg) == pcm(webm)


def test_a_bad_source_raises_and_leaves_nothing(tmp_path):
    bad = tmp_path / "audio_1.0.opus"
    bad.write_bytes(b"not audio")
    with pytest.raises(webm_remux.RemuxError):
        webm_remux.remux_tier(bad)
    assert list(tmp_path.glob("*.webm*")) == []


def test_remux_tiers_is_all_or_nothing(tmp_path):
    good = make_ogg(tmp_path / "audio_1.0.opus")
    bad = tmp_path / "audio_2.0.opus"
    bad.write_bytes(b"nope")
    with pytest.raises(webm_remux.RemuxError):
        webm_remux.remux_tiers([good, bad], max_workers=1)
    assert list(tmp_path.glob("*.webm")) == []


def test_available_formats_needs_every_tier(tmp_path):
    assert webm_remux.available_formats(tmp_path) == ("opus",)  # nothing at all
    a, b = make_ogg(tmp_path / "audio_1.0.opus"), make_ogg(tmp_path / "audio_2.0.opus")
    assert webm_remux.available_formats(tmp_path) == ("opus",)
    webm_remux.remux_tier(a)
    assert webm_remux.available_formats(tmp_path) == ("opus",)  # half converted: still Ogg
    webm_remux.remux_tier(b)
    assert webm_remux.available_formats(tmp_path) == ("webm", "opus")
    webm_remux.webm_path(b).write_bytes(b"")  # an empty copy does not count
    assert webm_remux.available_formats(tmp_path) == ("opus",)


def test_env_switch(monkeypatch):
    monkeypatch.setenv("RENDER_WEBM", "0")
    assert not webm_remux.enabled()
    monkeypatch.setenv("RENDER_WEBM", "1")
    assert webm_remux.enabled()


def test_manifest_cache_keys_on_formats(tmp_path, monkeypatch):
    import gzip, json
    monkeypatch.setenv("RENDER_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "manifest.json"
    src.write_text(json.dumps({"schema": 2, "duration_ms": 1, "tiers": {}, "segments": []}))
    read = lambda c: json.loads(gzip.decompress(c.path.read_bytes() if c.path else c.data))
    plain = manifest_cache.get("b", 1, src)
    assert "audio_formats" not in read(plain)
    both = manifest_cache.get("b", 1, src, ("webm", "opus"))  # replaces the plain entry
    assert plain.etag != both.etag
    assert read(both)["audio_formats"] == ["webm", "opus"]


def test_worker_step_never_fails_a_render(tmp_path, monkeypatch):
    """A remux failure leaves an Ogg-only render instead of failing the job."""
    from services import render_worker
    ogg = make_ogg(tmp_path / "audio_1.0.opus")
    info = {"1.0": {"path": ogg}}
    render_worker._add_webm_copies(tmp_path, info, "b")
    assert (tmp_path / "audio_1.0.webm").is_file()

    (tmp_path / "audio_1.0.webm").unlink()
    monkeypatch.setattr(webm_remux, "remux_tiers", lambda files: (_ for _ in ()).throw(webm_remux.RemuxError("boom")))
    render_worker._add_webm_copies(tmp_path, info, "b")  # no raise
    assert not (tmp_path / "audio_1.0.webm").exists()

    monkeypatch.setenv("RENDER_WEBM", "0")
    monkeypatch.undo()
    monkeypatch.setenv("RENDER_WEBM", "0")
    render_worker._add_webm_copies(tmp_path, info, "b")
    assert not (tmp_path / "audio_1.0.webm").exists()
