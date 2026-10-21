"""GET /library/{id}/render: slim manifest, gzip, cache outside the render, ETag."""
import gzip
import hashlib
import json

import pytest

import storage
from services import manifest_cache
from services import render_manifest as rm
from tests.test_render_manifest import ODD_FACTORS, _v1

BID = "test-book"


@pytest.fixture
def renders_dir(tmp_path, monkeypatch):
    d = tmp_path / "renders"
    d.mkdir()
    monkeypatch.setattr(storage, "RENDERS_DIR", d)
    monkeypatch.delenv("RENDER_CACHE_DIR", raising=False)
    return d


def _publish(renders_dir, manifest, gen=1):
    d = renders_dir / BID / str(gen)
    d.mkdir(parents=True)
    p = d / "manifest.json"
    p.write_text(json.dumps(manifest))
    return p


def _tree_digest(d):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


def test_v1_render_is_served_slim_and_equal_per_tier(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    v1 = _v1()
    _publish(renders_dir, v1)
    before = _tree_digest(renders_dir)
    r = client.get(f"/library/{BID}/render")
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == 2 and "segments" in body
    assert all("segments" not in t for t in body["tiers"].values())
    for k in ODD_FACTORS:
        assert rm.tier_segments(body, k) == v1["tiers"][k]["segments"]
    assert _tree_digest(renders_dir) == before  # the published generation is untouched
    assert list(manifest_cache.cache_dir().glob(f"{BID}/*.json.gz"))
    assert not manifest_cache.cache_dir().is_relative_to(renders_dir)


def test_gzip_vary_and_etag(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    _publish(renders_dir, _v1())
    r = client.get(f"/library/{BID}/render", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in r.headers["vary"].lower()
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["schema"] == 2  # the client decoded it
    etag = r.headers["etag"]
    assert etag.startswith('"slim2-1-')
    r2 = client.get(f"/library/{BID}/render", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.headers["etag"] == etag
    assert client.get(f"/library/{BID}/render",
                      headers={"If-None-Match": '"other"'}).status_code == 200


def test_identity_when_gzip_not_accepted(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    _publish(renders_dir, _v1())
    r = client.get(f"/library/{BID}/render", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200 and "content-encoding" not in r.headers
    assert json.loads(r.content)["schema"] == 2


def test_cache_is_reused_and_follows_the_generation(client, db_engine, seed_book, renders_dir,
                                                    monkeypatch):
    seed_book(db_engine, ready_generation=1)
    _publish(renders_dir, _v1())
    calls = []
    real = manifest_cache.slim_gzip
    monkeypatch.setattr(manifest_cache, "slim_gzip", lambda p: calls.append(p) or real(p))
    e1 = client.get(f"/library/{BID}/render").headers["etag"]
    client.get(f"/library/{BID}/render")
    assert len(calls) == 1
    _publish(renders_dir, rm.slim_manifest(_v1(n=5)), gen=2)
    from sqlmodel import Session
    from db.models import Book
    with Session(db_engine) as s:
        b = s.get(Book, BID)
        b.ready_generation = 2
        s.add(b)
        s.commit()
    r = client.get(f"/library/{BID}/render")
    assert r.status_code == 200 and len(r.json()["segments"]) == 5
    assert r.headers["etag"] != e1 and len(calls) == 2
    assert [p.name.split("-")[0] for p in (manifest_cache.cache_dir() / BID).glob("*.json.gz")] == ["2"]


def test_schema2_render_served_as_is(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    slim = rm.slim_manifest(_v1())
    _publish(renders_dir, slim)
    assert client.get(f"/library/{BID}/render").json() == json.loads(json.dumps(slim))


def test_missing_and_corrupt_manifest_404(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    assert client.get(f"/library/{BID}/render").status_code == 404
    p = renders_dir / BID / "1"
    p.mkdir(parents=True)
    (p / "manifest.json").write_text("{not json")
    assert client.get(f"/library/{BID}/render").status_code == 404


def test_delete_book_removes_its_cache(client, db_engine, seed_book, renders_dir):
    seed_book(db_engine, ready_generation=1)
    _publish(renders_dir, _v1())
    client.get(f"/library/{BID}/render")
    assert (manifest_cache.cache_dir() / BID).is_dir()
    assert client.delete(f"/library/{BID}").status_code == 200
    assert not (manifest_cache.cache_dir() / BID).exists()


def test_cache_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert manifest_cache.cache_dir() == tmp_path / "elsewhere"


@pytest.mark.parametrize("header,ok", [
    ("gzip, deflate, br", True), ("br;q=1.0, gzip;q=0.8", True), ("gzip;q=0", False),
    ("identity", False), (None, False), ("*", True), ("gzip;q=junk", True)])
def test_accepts_gzip(header, ok):
    assert manifest_cache.accepts_gzip(header) is ok


def test_etag_matches():
    assert manifest_cache.etag_matches('"a", W/"b"', '"b"')
    assert manifest_cache.etag_matches("*", '"b"')
    assert not manifest_cache.etag_matches(None, '"b"')


def test_slim_gzip_roundtrip(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_v1()))
    assert json.loads(gzip.decompress(manifest_cache.slim_gzip(p)))["schema"] == 2


def test_unwritable_cache_still_serves(client, db_engine, seed_book, renders_dir, monkeypatch):
    seed_book(db_engine, ready_generation=1)
    _publish(renders_dir, _v1())
    root = manifest_cache.cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o555)
    try:
        r = client.get(f"/library/{BID}/render", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.json()["schema"] == 2
        ident = client.get(f"/library/{BID}/render", headers={"Accept-Encoding": "identity"})
        assert ident.status_code == 200 and ident.headers["etag"] != r.headers["etag"]
    finally:
        root.chmod(0o755)
