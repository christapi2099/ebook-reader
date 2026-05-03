"""Tests for the voices router."""
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestListVoices:
    def test_list_returns_28_english_voices(self, client):
        r = client.get("/voices")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 28
        built_in = [v for v in data if v["built_in"]]
        assert len(built_in) == 28

    def test_every_voice_has_required_fields(self, client):
        r = client.get("/voices")
        for v in r.json():
            for field in ("id", "name", "lang", "gender", "quality", "built_in"):
                assert field in v, f"Missing {field} in {v['id']}"

    def test_includes_af_heart(self, client):
        r = client.get("/voices")
        ids = [v["id"] for v in r.json()]
        assert "af_heart" in ids


class TestUploadVoice:
    def test_upload_non_pt_returns_400(self, client):
        r = client.post("/voices/upload", files={"file": ("test.txt", b"hello", "text/plain")})
        assert r.status_code == 400

    def test_upload_valid_pt_returns_200(self, client):
        r = client.post("/voices/upload", files={"file": ("test_voice.pt", b"fakeptdata", "application/octet-stream")})
        assert r.status_code == 200
        data = r.json()
        assert data["id"].startswith("custom:")

    def test_uploaded_voice_appears_in_list(self, client):
        client.post("/voices/upload", files={"file": ("custom1.pt", b"data1", "application/octet-stream")})
        r = client.get("/voices")
        ids = [v["id"] for v in r.json()]
        assert "custom:custom1" in ids


class TestDeleteVoice:
    def test_delete_built_in_returns_403(self, client):
        r = client.delete("/voices/af_heart")
        assert r.status_code == 403

    def test_delete_custom_voice(self, client):
        client.post("/voices/upload", files={"file": ("delme.pt", b"deldata", "application/octet-stream")})
        r = client.delete("/voices/custom:delme")
        assert r.status_code == 200

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/voices/custom:nonexistent")
        assert r.status_code == 404


class TestPreviewVoice:
    def test_preview_built_in_returns_audio(self, client):
        r = client.get("/voices/preview/af_heart")
        # May return 503 if Kokoro is not loaded; skip in CI
        if r.status_code == 503:
            pytest.skip("Kokoro not available")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/wav"
        assert len(r.content) > 100

    def test_preview_nonexistent_custom_returns_404(self, client):
        r = client.get("/voices/preview/custom:nonexistent")
        assert r.status_code == 404
