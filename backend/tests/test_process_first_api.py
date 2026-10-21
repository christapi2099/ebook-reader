from datetime import datetime, timedelta, timezone
"""Process-first API: triggers, status fields, the read gate, render audio serving."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402
from db.models import Book, Progress  # noqa: E402
from routers import renders as renders_router  # noqa: E402
from services import render_stats, render_triggers  # noqa: E402

BID = "test-book"
REAL_SWEEP = render_triggers.startup_sweep  # conftest patches the module attribute


@pytest.fixture
def renders_dir(tmp_path, monkeypatch):
    d = tmp_path / "renders"
    d.mkdir()
    monkeypatch.setattr(storage, "RENDERS_DIR", d)
    return d


def get(engine, bid=BID):
    with Session(engine) as s:
        s.expire_on_commit = False
        b = s.get(Book, bid)
        s.refresh(b)
        return b


def ready_book(seed_book, engine, voice="af_heart", **kw):
    fp = render_triggers.target_fingerprint_for(voice)
    seed_book(engine, ready_generation=1, ready_fingerprint=fp, voice=voice,
              job_status="idle", **kw)
    return fp


def write_gen(renders_dir, bid=BID, gen=1, tier="1.0", data=b"OggS" + bytes(range(256)) * 4):
    d = renders_dir / bid / str(gen)
    d.mkdir(parents=True)
    (d / f"audio_{tier}.opus").write_bytes(data)
    (d / "manifest.json").write_text(json.dumps({"generation": gen, "tiers": {tier: {}}}))
    return data


# --- state machine: upload -> pending ---------------------------------------

def test_upload_sets_target_and_pending_without_debounce(client, db_engine, sample_pdf_path):
    r = client.post("/documents/upload",
                    files={"file": ("a.pdf", sample_pdf_path.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    b = get(db_engine, r.json()["book_id"])
    assert b.job_status == "pending"
    assert b.target_fingerprint == render_triggers.target_fingerprint_for(None)
    assert b.pending_since is None  # debounce=False
    assert b.ready_generation is None
    assert client.get(f"/library/{b.id}").json()["needs_render"] is True


def test_patch_voice_requests_render_and_book_stays_readable(client, db_engine, seed_book):
    ready_book(seed_book, db_engine)
    r = client.patch(f"/library/{BID}", json={"voice": "bf_emma"})
    assert r.status_code == 200
    body = r.json()
    assert body["job_status"] == "pending" and body["needs_render"] is True
    assert body["ready_generation"] == 1 and body["voice"] == "bf_emma"
    assert client.get(f"/documents/{BID}/sentences").status_code == 200  # old generation


def test_flip_back_cancels_via_patch(client, db_engine, seed_book):
    ready_book(seed_book, db_engine)
    client.patch(f"/library/{BID}", json={"voice": "bf_emma"})
    assert get(db_engine).job_status == "pending"
    body = client.patch(f"/library/{BID}", json={"voice": "af_heart"}).json()
    assert body["job_status"] == "cancelled" and body["needs_render"] is False


def test_patch_voice_rejects_blank(client, db_engine, seed_book):
    ready_book(seed_book, db_engine)
    assert client.patch(f"/library/{BID}", json={"voice": "  "}).status_code == 400


def test_reprocess_and_cancel(client, db_engine, seed_book):
    ready_book(seed_book, db_engine)
    r = client.post(f"/library/{BID}/reprocess")
    # Same fingerprint as the ready render: a no-op request cancels, never loops.
    assert r.status_code == 200 and r.json()["job_status"] == "cancelled"
    client.patch(f"/library/{BID}", json={"voice": "bf_emma"})
    assert client.post(f"/library/{BID}/reprocess").json()["job_status"] == "pending"
    assert get(db_engine).pending_since is None
    assert client.post(f"/library/{BID}/reprocess?cancel=true").json()["job_status"] == "cancelled"
    assert client.post("/library/nope/reprocess").status_code == 404


def test_engine_family_switch_marks_mismatched_books(client, db_engine, seed_book, monkeypatch):
    ready_book(seed_book, db_engine)
    assert render_triggers.retarget_all() == []  # nothing stale
    monkeypatch.setattr(render_triggers, "current_engine_family", lambda: "piper")
    # The finished render is kept; it is not re-made just because the engine changed.
    assert render_triggers.retarget_all() == []
    b = get(db_engine)
    assert b.job_status == "idle" and b.ready_generation == 1


def test_engine_endpoint_hooks_retarget(client, db_engine, seed_book, monkeypatch):
    from services import engine_manager
    ready_book(seed_book, db_engine)
    monkeypatch.setattr(engine_manager.manager, "switch", lambda e: None)
    monkeypatch.setattr(render_triggers, "current_engine_family", lambda: "piper")
    assert client.post("/api/system/engine", json={"engine": "cpu"}).status_code == 200
    assert get(db_engine).job_status == "idle"  # the finished render is not re-made


def test_startup_sweep_requeues_and_reconciles(db_engine, seed_book, renders_dir, monkeypatch):
    import db.database as _db
    monkeypatch.setattr(_db, "engine", db_engine)
    ready_book(seed_book, db_engine)  # ready but no artifacts on disk
    seed_book(db_engine, book_id="b2", ready_generation=None, job_status="idle")
    REAL_SWEEP()
    a, b = get(db_engine), get(db_engine, "b2")
    assert a.ready_generation is None and a.job_status == "pending"  # reconcile
    assert b.job_status == "pending" and b.target_fingerprint  # retarget


# --- status fields ------------------------------------------------------------

def test_status_fields_and_queue_position(client, db_engine, seed_book):
    seed_book(db_engine, book_id="p1", ready_generation=None, job_status="processing",
              progress=0.5, phase="synth", render_priority=1)
    seed_book(db_engine, book_id="p2", ready_generation=None, job_status="pending")
    rows = {r["id"]: r for r in client.get("/library").json()}
    for k in ("ready_generation", "job_status", "progress", "phase", "eta_seconds",
              "queue_position", "needs_render"):
        assert k in rows["p1"]
    assert rows["p1"]["queue_position"] == 1 and rows["p2"]["queue_position"] == 2


def test_get_book_bumps_priority(client, db_engine, seed_book):
    seed_book(db_engine, book_id="a", ready_generation=None, job_status="pending", render_priority=5)
    seed_book(db_engine, book_id="b", ready_generation=None, job_status="pending")
    body = client.get("/library/b").json()
    assert get(db_engine, "b").render_priority == 6 and body["queue_position"] == 1


def test_processing_endpoint(client, db_engine, seed_book, renders_dir):
    live = datetime.now(timezone.utc) + timedelta(hours=1)  # a worker really holds it
    seed_book(db_engine, ready_generation=None, job_status="processing", progress=0.5,
              lease_expires=live)
    render_stats.record(BID, sentences_done=0, sentences_total=3, now=100.0)
    render_stats.record(BID, sentences_done=10, phase_timings={"synth": 1.5}, now=105.0,
                        last_error="Traceback...")
    p = client.get(f"/library/{BID}/processing").json()
    assert p["sentences_done"] == 10 and p["sentences_total"] == 3
    assert p["sentences_per_second"] == pytest.approx(2.0)
    assert p["phase_timings"] == {"synth": 1.5} and p["last_error"] == "Traceback..."
    assert client.get("/library/nope/processing").status_code == 404
    # eta from the EWMA on the list/detail view
    seed_book(db_engine, book_id="e", ready_generation=None, job_status="processing", progress=0.5,
              lease_expires=live)
    render_stats.record("e", sentences_done=0, sentences_total=100, now=0.0)
    render_stats.record("e", sentences_done=10, now=5.0)
    # 90 sentences left at 2 per second; the old progress-fraction formula gave 25
    assert client.get("/library/e").json()["eta_seconds"] == 45


def test_list_survives_a_decayed_rate(client, db_engine, seed_book, monkeypatch):
    # A no-progress phase (encode) decays the EWMA toward zero; the list must not 500.
    live = datetime.now(timezone.utc) + timedelta(hours=1)
    seed_book(db_engine, ready_generation=None, job_status="processing", lease_expires=live)
    monkeypatch.setattr(render_stats, "read", lambda _id: {
        "sps_ewma": 1e-286, "sentences_total": 18539, "sentences_done": 100})
    r = client.get("/library")
    assert r.status_code == 200 and r.json()[0]["eta_seconds"] is None


# --- the read gate -------------------------------------------------------------

def test_sentences_409_without_ready_generation(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=None)
    assert client.get(f"/documents/{BID}/sentences?generation=1").status_code == 409
    assert client.get(f"/library/{BID}/render").status_code == 409


def test_sentences_ok_with_ready_generation(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=2)
    r = client.get(f"/documents/{BID}/sentences?generation=2")
    assert r.status_code == 200 and r.headers["x-render-generation"] == "2"


def test_ws_closes_4409_without_render_but_serves_old_generation(
        ws_client_factory, ws_engine):
    from starlette.websockets import WebSocketDisconnect
    with ws_client_factory() as c:
        with Session(ws_engine) as s:  # re-rendering: pending job, ready generation kept
            b = s.get(Book, BID)
            b.job_status = "pending"
            s.add(b)
            s.commit()
        with c.websocket_connect(f"/ws/tts/{BID}") as ws:
            ws.send_json({"action": "pause"})  # accepted: old generation still readable
    with ws_client_factory(book_id="raw") as c:
        with Session(ws_engine) as s:
            b = s.get(Book, "raw")
            b.ready_generation = None
            s.add(b)
            s.commit()
        with pytest.raises(WebSocketDisconnect) as ei:
            with c.websocket_connect("/ws/tts/raw") as ws:
                ws.receive_text()
        assert ei.value.code == 4409


def test_manifest_endpoint(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    assert client.get(f"/library/{BID}/render").status_code == 404
    write_gen(renders_dir)
    assert client.get(f"/library/{BID}/render").json()["generation"] == 1


# --- generation-stamped writes ---------------------------------------------------

def test_progress_write_stamped_and_old_generation_not_rejected(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=3)
    r = client.post(f"/library/{BID}/progress", json={"sentence_index": 1, "generation": 1})
    assert r.status_code == 200 and r.json()["sentence_index"] == 1
    with Session(db_engine) as s:
        assert s.get(Progress, BID).generation == 3


def test_progress_write_uses_remap_when_available(client, db_engine, seed_book, monkeypatch):
    import types
    seed_book(db_engine, ready_generation=3)
    mod = types.ModuleType("services.remap")
    mod.translate_progress_write = lambda book_id, gen, idx: 2
    monkeypatch.setitem(sys.modules, "services.remap", mod)
    r = client.post(f"/library/{BID}/progress", json={"sentence_index": 0, "generation": 1})
    assert r.json()["sentence_index"] == 2


def test_user_settings_store_generation(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=2)
    client.post("/user/settings", json={"last_book_id": BID, "last_sentence_index": 1,
                                         "last_generation": 2})
    s = client.get("/user/settings").json()
    assert (s["last_book_id"], s["last_sentence_index"], s["last_generation"]) == (BID, 1, 2)


def test_bookmark_stamped(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=2)
    b = client.post("/bookmarks", json={"book_id": BID, "sentence_index": 1, "label": "x",
                                         "generation": 1}).json()
    assert b["generation"] == 2


# --- /api/renders: validation, immutability, ranges ----------------------------------

BAD_IDS = ["..", "../x", "a/b", "/etc/passwd", "a" * 65, "a b", "a.b", ".."]


@pytest.mark.parametrize("bid", BAD_IDS)
def test_rejects_bad_book_ids(bid, renders_dir):
    with pytest.raises(HTTPException) as e:
        renders_router.resolve_audio_path(bid, "1", "1.0")
    assert e.value.status_code == 400


@pytest.mark.parametrize("gen", ["x", "1.5", "-1", "../1", "", "1e3", "١"])
def test_rejects_non_integer_gen(gen, renders_dir):
    with pytest.raises(HTTPException) as e:
        renders_router.resolve_audio_path(BID, gen, "1.0")
    assert e.value.status_code == 400


@pytest.mark.parametrize("tier", ["9.0", "../1.0", "1", "0.5", "1.0/../..", "", "abc", "/etc/passwd"])
def test_rejects_bad_tiers(tier, renders_dir):
    with pytest.raises(HTTPException) as e:
        renders_router.resolve_audio_path(BID, "1", tier)
    assert e.value.status_code == 400


@pytest.mark.parametrize("tier", ["1.0", "0.75", "1.25", "1.5", "1.75", "2.0", "2.5", "3.0"])
def test_old_and_new_tiers_are_servable(tier, renders_dir):
    """Old renders keep their 0.75/1.25/1.75 files; new ones build 1.5..3.0."""
    path = renders_router.resolve_audio_path(BID, "1", tier)
    assert path.name == f"audio_{tier}.opus"


def test_rejects_symlink_escape(renders_dir, tmp_path):
    (tmp_path / "secret").mkdir()
    (renders_dir / "evil").symlink_to(tmp_path / "secret")
    with pytest.raises(HTTPException) as e:
        renders_router.resolve_audio_path("evil", "1", "1.0")
    assert e.value.status_code == 400


@pytest.mark.parametrize("url", [
    "/api/renders/../../etc/passwd/1/audio_1.0.opus",
    "/api/renders/..%2f..%2fetc/1/audio_1.0.opus",
    "/api/renders/%2fetc%2fpasswd/1/audio_1.0.opus",
    f"/api/renders/{BID}/abc/audio_1.0.opus",
    f"/api/renders/{BID}/1/audio_9.9.opus",
    f"/api/renders/{BID}/1/audio_..%2f1.0.opus",
])
def test_http_rejects_traversal(client, renders_dir, url):
    assert client.get(url).status_code in (400, 404)


def test_http_bad_gen_and_tier_are_400(client, renders_dir):
    assert client.get(f"/api/renders/{BID}/abc/audio_1.0.opus").status_code == 400
    assert client.get(f"/api/renders/{BID}/1/audio_9.9.opus").status_code == 400
    assert client.get(f"/api/renders/{BID}/1/audio_1.0.opus").status_code == 404


def test_audio_immutable_etag_and_304(client, renders_dir):
    data = write_gen(renders_dir, tier="1.5")
    r = client.get(f"/api/renders/{BID}/1/audio_1.5.opus")
    assert r.status_code == 200 and r.content == data
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    etag = r.headers["etag"]
    assert etag
    r2 = client.get(f"/api/renders/{BID}/1/audio_1.5.opus", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_audio_range_206_last_bytes(client, renders_dir):
    data = write_gen(renders_dir, tier="0.75")
    n = len(data)
    r = client.get(f"/api/renders/{BID}/1/audio_0.75.opus", headers={"Range": "bytes=-100"})
    assert r.status_code == 206
    assert r.content == data[-100:]
    assert r.headers["content-range"] == f"bytes {n - 100}-{n - 1}/{n}"
    assert r.headers["accept-ranges"] == "bytes"
    assert "immutable" in r.headers["cache-control"]
    r = client.get(f"/api/renders/{BID}/1/audio_0.75.opus", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == data[10:20]


def test_webm_route_serves_the_webm_file_with_its_media_type(client, renders_dir):
    data = write_gen(renders_dir, tier="2.0")
    webm = b"\x1aE\xdf\xa3" + bytes(range(100))
    (renders_dir / BID / "1" / "audio_2.0.webm").write_bytes(webm)
    r = client.get(f"/api/renders/{BID}/1/audio_2.0.webm")
    assert r.status_code == 200 and r.content == webm
    assert r.headers["content-type"].startswith("audio/webm") and "immutable" in r.headers["cache-control"]
    ogg = client.get(f"/api/renders/{BID}/1/audio_2.0.opus")
    assert ogg.content == data and ogg.headers["content-type"].startswith("audio/ogg")
    assert r.headers["etag"] != ogg.headers["etag"]
    r = client.get(f"/api/renders/{BID}/1/audio_2.0.webm", headers={"Range": "bytes=2-9"})
    assert r.status_code == 206 and r.content == webm[2:10]
    assert client.get(f"/api/renders/{BID}/1/audio_9.9.webm").status_code == 400
    assert client.get(f"/api/renders/{BID}/1/audio_1.0.webm").status_code == 404


def test_audio_open_ended_range_is_capped_and_resumable(client, renders_dir, monkeypatch):
    """Firefox asks for bytes=N- and reads to EOF; the server answers with a
    bounded 206 and the browser reopens at the new offset."""
    monkeypatch.setattr(renders_router, "MAX_RANGE_BYTES", 100)
    data = write_gen(renders_dir, tier="2.0", data=bytes(range(256)) * 4)  # 1024 bytes
    n = len(data)
    url = f"/api/renders/{BID}/1/audio_2.0.opus"
    r = client.get(url, headers={"Range": "bytes=0-"})
    assert r.status_code == 206 and r.content == data[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{n}"
    assert r.headers["content-length"] == "100"
    assert "immutable" in r.headers["cache-control"] and r.headers["etag"]
    r = client.get(url, headers={"Range": "bytes=500-"})
    assert r.content == data[500:600] and r.headers["content-range"] == f"bytes 500-599/{n}"
    # a long explicit range is cut too; a short one, the tail and EOF are untouched
    assert client.get(url, headers={"Range": "bytes=10-900"}).content == data[10:110]
    assert client.get(url, headers={"Range": "bytes=10-19"}).content == data[10:20]
    assert client.get(url, headers={"Range": "bytes=-50"}).content == data[-50:]
    r = client.get(url, headers={"Range": "bytes=950-"})
    assert r.status_code == 206 and r.content == data[950:]
    assert client.get(url, headers={"Range": "bytes=5000-"}).status_code == 416
    assert client.get(url).content == data  # no Range: the whole file


def test_progress_reports_the_page_of_the_saved_sentence(client, db_engine, seed_book):
    """The library's resume card shows this page, so it must be the saved sentence's
    page, not the book's last page (a regression: the card said "Page 91" for a
    position on page 8)."""
    from sqlmodel import Session, select
    from db.models import Sentence
    ready_book(seed_book, db_engine)
    with Session(db_engine) as s:
        rows = s.exec(select(Sentence).where(Sentence.book_id == BID)).all()
        for r in rows:
            r.page = r.index // 10  # ten sentences per page
            s.add(r)
        s.commit()
    client.post(f"/library/{BID}/progress", json={"sentence_index": 23})
    body = client.get(f"/library/{BID}/progress").json()
    assert body["page"] == body["sentence_index"] // 10
