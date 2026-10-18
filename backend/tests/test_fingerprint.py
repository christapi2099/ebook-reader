"""Fingerprint invariants (process-first plan, Phase 1b)."""
import dataclasses

import pytest

from services import book_processor as bp


def _base(**kw):
    d = dict(
        voice_name="af_heart", voice_file_sha256="a" * 64, engine_family="kokoro",
        model_revision="r1", aligner_name="faster-whisper", aligner_version="1.0",
        aligner_model="small.en", ffmpeg_version="7.0", pymupdf_version="1.28.2",
    )
    d.update(kw)
    return bp.FingerprintInputs(**d)


def test_stable_and_hex():
    a = bp.fingerprint(_base())
    assert a == bp.fingerprint(_base())
    assert len(a) == 64 and int(a, 16) >= 0


@pytest.mark.parametrize("change", [
    {"voice_name": "am_adam"},
    {"voice_file_sha256": "b" * 64},
    {"engine_family": "piper"},
    {"codec": "mp3"},
    {"bitrate": "96k"},
    {"sample_rate": 48000},
    {"tiers": (0.75, 1.5)},
    {"base_effective_speed": 1.1},
    {"gap_policy": "other"},
    {"model_revision": "r2"},
    {"aligner_version": "2.0"},
    {"aligner_name": "ctc"},
    {"aligner_model": "base.en"},
    {"ffmpeg_version": "8.0"},
    {"pymupdf_version": "1.99"},
    {"pipeline_version": "x" * 64},
])
def test_changes_with_input(change):
    assert bp.fingerprint(_base(**change)) != bp.fingerprint(_base())


def test_device_and_speed_are_not_inputs():
    names = {f.name for f in dataclasses.fields(bp.FingerprintInputs)}
    assert not names & {"device", "speed", "compute_device", "backend"}
    with pytest.raises(TypeError):
        _base(device="gpu")
    with pytest.raises(TypeError):
        _base(speed=2.0)


def test_pipeline_version_derived_from_source(monkeypatch):
    before = bp.compute_pipeline_version()
    assert before == bp.compute_pipeline_version()
    real = bp._read_source
    def edited(name):
        src = real(name)
        return src + "\n# new TextFilter rule\n" if name == "text_filter.py" else src
    monkeypatch.setattr(bp, "_read_source", edited)
    after = bp.compute_pipeline_version()
    assert after != before
    assert bp.fingerprint(_base(pipeline_version=after)) != bp.fingerprint(
        _base(pipeline_version=before))


def test_current_inputs_defaults():
    i = bp.current_inputs("af_heart", "kokoro", ffmpeg_version="7.0",
                          voices_dir="/nonexistent")
    assert i.pipeline_version == bp.PIPELINE_VERSION
    assert i.tiers == bp.TIERS
    assert i.pymupdf_version
    # built-in voice: hash of the name string
    import hashlib
    assert i.voice_file_sha256 == hashlib.sha256(b"af_heart").hexdigest()


def test_current_inputs_voice_file_content(tmp_path):
    f = tmp_path / "v.onnx"
    f.write_bytes(b"one")
    a = bp.current_inputs("v", "piper", ffmpeg_version="7", voice_path=str(f))
    f.write_bytes(b"two")
    b = bp.current_inputs("v", "piper", ffmpeg_version="7", voice_path=str(f))
    assert a.voice_file_sha256 != b.voice_file_sha256
    assert bp.fingerprint(a) != bp.fingerprint(b)


def test_current_inputs_pymupdf_version_flows(monkeypatch):
    a = bp.current_inputs("v", "kokoro", ffmpeg_version="7")
    monkeypatch.setattr(bp, "_pymupdf_version", lambda: "9.9.9")
    b = bp.current_inputs("v", "kokoro", ffmpeg_version="7")
    assert bp.fingerprint(a) != bp.fingerprint(b)


@pytest.mark.skip(reason="needs Modal + local Kokoro; run manually after Checkpoint 2")
def test_local_vs_modal_word_timestamps_agree():
    pass


def test_current_inputs_aligner_defaults_are_real():
    from importlib import metadata
    i = bp.current_inputs("af_heart", "kokoro", ffmpeg_version="7.0", voices_dir="/nonexistent")
    assert i.aligner_name == "ctc-forced-aligner"
    assert i.aligner_version == metadata.version("ctc-forced-aligner") != "unknown"
    assert "MMS" in i.aligner_model
