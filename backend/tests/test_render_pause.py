"""The user's pause for background rendering: the flag, and the endpoint the UI uses."""
import storage
from services import render_pause


def test_flag_round_trips_and_survives_a_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")  # folder not created yet
    assert render_pause.is_paused() is False
    assert render_pause.set_paused(True) is True
    assert render_pause.is_paused() is True
    assert render_pause.set_paused(False) is False
    assert render_pause.is_paused() is False


def test_endpoint_reads_and_sets_the_pause(client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "RENDERS_DIR", tmp_path / "renders")
    assert client.get("/api/system/render-pause").json() == {"paused": False}
    assert client.post("/api/system/render-pause", json={"paused": True}).json() == {"paused": True}
    assert client.get("/api/system/render-pause").json() == {"paused": True}
    assert client.post("/api/system/render-pause", json={"paused": False}).json() == {"paused": False}
