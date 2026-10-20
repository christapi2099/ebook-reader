import importlib.util
import json
import sys
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import Session

import db.database as _db
import storage
from db.models import Book, Sentence
from services import export_encoding, render_artifacts as ra, render_manifest

_spec = importlib.util.spec_from_file_location(
    "verify_render", Path(__file__).resolve().parent.parent / "scripts" / "verify_render.py")
vr = importlib.util.module_from_spec(_spec)
sys.modules["verify_render"] = vr
_spec.loader.exec_module(vr)

SR = 24000
N = 12
FP = "fp-good"


def _chunks():
    out = []
    for i in range(N):
        dur_ms = 700 + 40 * (i % 5)
        t = np.arange(int(SR * dur_ms / 1000)) / SR
        a = (0.2 * np.sin(2 * np.pi * (300 + 20 * i) * t)).astype(np.float32)
        words = [{"word": "w", "start_ms": k * dur_ms / 3, "end_ms": (k + 1) * dur_ms / 3 - 5,
                  "box": [0, 0, 1, 1], "timing_source": "kokoro"} for k in range(3)]
        out.append({"index": i, "audio": a, "words": words, "chapter": 0 if i < 6 else 1,
                    "text": f"sentence {i}."})
    return out


@pytest.fixture
def render(real_ffmpeg, tmp_path, monkeypatch):
    assert export_encoding.ffmpeg_available(), "ffmpeg required"
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    old = _db.engine
    _db.create_engine_and_tables(f"sqlite:///{tmp_path/'t.db'}")
    gdir = tmp_path / "renders" / "b1" / "1"
    gdir.mkdir(parents=True)
    base = ra.build_base(_chunks(), gdir)
    tiers = ra.build_tiers(base["path"], base["segments"], (1.5, 2.0), gdir)
    ra.write_manifest(gdir, base_segments=base["segments"], tier_results=tiers,
                      duration_ms=base["duration_ms"], voice="v", voice_file_sha256="x",
                      fingerprint=FP)
    with Session(_db.engine) as s:
        s.add(Book(id="b1", title="t", file_path="x", file_type="pdf", page_count=1,
                   created_at=datetime.now(UTC), ready_generation=1, ready_fingerprint=FP))
        for i in range(N):
            s.add(Sentence(book_id="b1", index=i, text=f"sentence {i}.", page=0,
                           x0=0, y0=0, x1=1, y1=1, chapter=0 if i < 6 else 1))
        s.commit()
    yield gdir
    _db.engine.dispose()
    _db.engine = old


def _edit_manifest(gdir, fn):
    """Edit the schema 1 view (every tier with segments) and write it back slim:
    a tier edited out of step with 1x / actual_factor keeps its own segments."""
    p = gdir / "manifest.json"
    m = render_manifest.expand_v1(json.loads(p.read_text()))
    fn(m)
    slim = render_manifest.slim_manifest(m)
    if m.get("schema") != 1:
        slim["schema"] = m["schema"]
    p.write_text(json.dumps(slim))


def _fails(substr):
    rep = vr.verify_render("b1")
    assert not rep.ok
    assert any(substr in f for f in rep.failures), rep.failures
    return rep


def test_good_render_passes(render, capsys):
    rep = vr.verify_render("b1")
    assert rep.ok, rep.failures
    assert vr.main(["b1"]) == 0
    assert "OK" in capsys.readouterr().out
    assert vr.main(["b1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_missing_tier_file(render, capsys):
    (render / "audio_1.5.opus").unlink()
    _fails("audio_1.5.opus missing")
    assert vr.main(["b1"]) == 1
    assert "audio_1.5.opus" in capsys.readouterr().out


def test_truncated_audio(render):
    f = render / "audio_2.0.opus"
    f.write_bytes(f.read_bytes()[: f.stat().st_size // 3])
    _fails("duration")


def test_corrupt_gap(render):
    _edit_manifest(render, lambda m: m["tiers"]["1.0"]["segments"][3].update(gap_ms=5000))
    _fails("starts")


def test_chapter_gap_too_short(render):
    _edit_manifest(render, lambda m: m["tiers"]["1.0"]["segments"][5].update(gap_ms=120))
    _fails("chapter boundary")


def test_wrong_fingerprint(render):
    _edit_manifest(render, lambda m: m.update(fingerprint="other"))
    _fails("fingerprint")


def test_missing_segment(render):
    _edit_manifest(render, lambda m: m["tiers"]["1.5"]["segments"].pop(4))
    _fails("segments missing")


def test_misc_invariants(render):
    def bad(m):
        m["base_effective_speed"] = 0.9
        m["schema"] = 3
        m["timing_quality"] = "estimated"
        m["tiers"]["2.0"]["max_drift_ms"] = 99.0
        m["tiers"]["2.0"]["actual_factor"] = 1.9
    _edit_manifest(render, bad)
    (render / "base.flac").unlink()
    (render.parent / "7").mkdir()
    rep = vr.verify_render("b1")
    text = "\n".join(rep.failures)
    for s in ("schema", "base_effective_speed", "base.flac", "orphan", "timing_quality",
              "max_drift_ms", "actual_factor"):
        assert s in text, (s, text)


def test_sentence_count_mismatch(render):
    with Session(_db.engine) as s:
        s.add(Sentence(book_id="b1", index=99, text="x", page=0, x0=0, y0=0, x1=1, y1=1))
        s.commit()
    _fails("unfiltered sentences")


def test_cli_binds_engine_when_run_standalone(render, monkeypatch, tmp_path, capsys):
    """Run as a script nothing has created the engine; main() must bind a read-only one."""
    db_file = tmp_path / "t.db"
    saved = _db.engine
    saved.dispose()
    monkeypatch.setattr(_db, "engine", None)
    monkeypatch.setattr(_db, "_DEFAULT_DB", str(db_file))
    assert vr.main(["b1"]) == 0
    assert "OK" in capsys.readouterr().out
    monkeypatch.setattr(_db, "engine", None)
    monkeypatch.setattr(_db, "_DEFAULT_DB", str(tmp_path / "missing.db"))
    assert vr.main(["b1"]) == 2
    assert not (tmp_path / "missing.db").exists()
    monkeypatch.setattr(_db, "engine", saved)
