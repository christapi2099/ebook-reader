"""Regression tests for path traversal in the voices router.

`/voices/{voice_id:path}` lets the id contain slashes, and both the delete and the
preview handler used to interpolate it straight into a filesystem path:

    dest = VOICES_DIR / f"{name}.pt"

so `custom:../victim` deleted `<voices>/../victim.pt` (any *.pt the process could
reach) and `custom:/abs/path` did the same with an absolute path. The preview
handler handed the escaped path to `torch.load` via the Kokoro pipeline.

These tests exercise the handlers directly (the traversal strings never survive an
HTTP client's URL normalisation) and, where the encoding does survive, through the
real ASGI stack.
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import voices as voices_router  # noqa: E402

TRAVERSAL_IDS = [
    "custom:../victim",
    "custom:../../victim",
    "custom:../../../../tmp/victim",
    "custom:a/../victim",
    "custom:sub/victim",
    "custom:..\\victim",
    "custom:/etc/victim",
    "custom:.",
    "custom:..",
]


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    """Redirect VOICES_DIR at a temp dir and drop a victim file beside it."""
    d = tmp_path / "voices"
    d.mkdir()
    victim = tmp_path / "victim.pt"
    victim.write_bytes(b"SECRET")
    monkeypatch.setattr(voices_router, "VOICES_DIR", d)
    return d, victim


@pytest.fixture
def app(voices_dir):
    app = FastAPI()
    app.include_router(voices_router.router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# DELETE /voices/{voice_id}
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("voice_id", TRAVERSAL_IDS)
def test_delete_rejects_traversal_and_absolute_paths(voice_id, voices_dir):
    voices_dir_path, victim = voices_dir
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice(voice_id)
    assert exc.value.status_code == 400, f"{voice_id} should be rejected, got {exc.value.status_code}"
    assert victim.exists(), "file outside VOICES_DIR must survive"


def test_delete_rejects_traversal_through_the_asgi_stack(client, voices_dir):
    """Percent-encoded so the traversal survives the HTTP client's normalisation.

    Verified to reach the handler (400 `Invalid voice id`), not to fall out at
    routing with a 404 — otherwise this test would pass vacuously.
    """
    _, victim = voices_dir
    r = client.delete("/voices/custom:%2E%2E%2Fvictim")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Invalid voice id"
    assert victim.exists(), "traversal reached the filesystem via HTTP"


def test_delete_rejects_symlink_escaping_the_voices_dir(voices_dir):
    """Containment must be checked on the RESOLVED path, not a string prefix."""
    d, victim = voices_dir
    (d / "escape.pt").symlink_to(victim)
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice("custom:escape")
    assert exc.value.status_code == 400
    assert victim.exists()


def test_delete_rejects_unexpected_extension(voices_dir):
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice("custom:payload.sh")
    assert exc.value.status_code == 400


def test_delete_rejects_null_byte(voices_dir):
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice("custom:good\x00.txt")
    assert exc.value.status_code == 400


# --- legitimate behaviour must be untouched ---

def test_delete_removes_a_real_custom_voice(voices_dir):
    d, _ = voices_dir
    target = d / "good.pt"
    target.write_bytes(b"voice")
    assert voices_router.delete_voice("custom:good") == {"ok": True}
    assert not target.exists()


def test_delete_accepts_an_id_that_already_carries_the_extension(voices_dir):
    d, _ = voices_dir
    target = d / "good.pt"
    target.write_bytes(b"voice")
    assert voices_router.delete_voice("custom:good.pt") == {"ok": True}
    assert not target.exists()


def test_delete_missing_voice_is_404(voices_dir):
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice("custom:nope")
    assert exc.value.status_code == 404


def test_delete_builtin_is_still_403(voices_dir):
    with pytest.raises(HTTPException) as exc:
        voices_router.delete_voice("af_heart")
    assert exc.value.status_code == 403


def test_delete_through_http_round_trip(client, voices_dir):
    d, _ = voices_dir
    (d / "http_voice.pt").write_bytes(b"voice")
    assert client.delete("/voices/custom:http_voice").status_code == 200
    assert not (d / "http_voice.pt").exists()


# ---------------------------------------------------------------------------
# GET /voices/preview/{voice_id}  (the torch.load sink)
# ---------------------------------------------------------------------------

@pytest.fixture
def preview_recorder(monkeypatch):
    seen = {}

    def kokoro(text, voice=None, speed=None):
        import numpy as np

        seen["voice"] = voice
        return [(None, None, np.zeros(240, dtype="float32"))]

    monkeypatch.setattr(voices_router, "_kokoro", kokoro)
    return seen


@pytest.mark.parametrize("voice_id", TRAVERSAL_IDS)
def test_preview_rejects_traversal_before_touching_the_engine(voice_id, voices_dir, preview_recorder):
    _, victim = voices_dir
    with pytest.raises(HTTPException) as exc:
        asyncio.run(voices_router.preview_voice(voice_id))
    assert exc.value.status_code == 400, f"{voice_id} should be rejected, got {exc.value.status_code}"
    assert "voice" not in preview_recorder, "engine received a voice argument for a rejected id"


def test_preview_rejects_symlink_escaping_the_voices_dir(voices_dir, preview_recorder):
    d, victim = voices_dir
    (d / "escape.pt").symlink_to(victim)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(voices_router.preview_voice("custom:escape"))
    assert exc.value.status_code == 400
    assert "voice" not in preview_recorder


def test_preview_passes_a_path_inside_the_voices_dir(voices_dir, preview_recorder):
    d, _ = voices_dir
    (d / "good.pt").write_bytes(b"voice")
    asyncio.run(voices_router.preview_voice("custom:good"))

    passed = Path(preview_recorder["voice"]).resolve()
    assert passed.is_relative_to(d.resolve()), f"{passed} escaped {d}"
    assert passed.name == "good.pt"


def test_preview_builtin_voice_id_is_forwarded_unchanged(voices_dir, preview_recorder):
    asyncio.run(voices_router.preview_voice("af_heart"))
    assert preview_recorder["voice"] == "af_heart"


def test_preview_missing_custom_voice_is_404(voices_dir, preview_recorder):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(voices_router.preview_voice("custom:nope"))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /voices/upload must mint ids the resolver accepts
# ---------------------------------------------------------------------------

def test_uploaded_id_round_trips_through_the_resolver(voices_dir, monkeypatch):
    import io

    from starlette.datastructures import UploadFile

    upload = UploadFile(filename="newvoice.pt", file=io.BytesIO(b"data"))
    result = asyncio.run(voices_router.upload_voice(upload))
    assert result["id"] == "custom:newvoice"

    # The id we handed back must be deletable.
    assert voices_router.delete_voice(result["id"]) == {"ok": True}
    assert not (voices_dir[0] / "newvoice.pt").exists()


def test_upload_rejects_a_filename_with_a_directory_component(voices_dir):
    import io

    from starlette.datastructures import UploadFile

    upload = UploadFile(filename="../evil.pt", file=io.BytesIO(b"data"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(voices_router.upload_voice(upload))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------

def test_resolver_returns_a_path_inside_the_voices_dir(voices_dir):
    d, _ = voices_dir
    resolved = voices_router._resolve_custom_voice_path("good")
    assert resolved == (d / "good.pt").resolve()
    assert resolved.is_relative_to(d.resolve())
