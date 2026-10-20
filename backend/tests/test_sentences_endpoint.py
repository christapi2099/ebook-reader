"""GET /documents/{id}/sentences: same payload, serialized directly, gzip when accepted."""
import gzip
import json

BID = "test-book"


def _rows():
    return [{"index": i, "text": f"Sentence number {i}.", "page": i // 3, "x0": 1.5 + i, "y0": 2.25,
             "x1": 300.125, "y1": 12.0, "filtered": i == 4, "chapter": i // 5,
             "chapter_title": "Intro" if i < 5 else None,
             "words": json.dumps([{"x0": 1.0 + k, "y0": 2.0, "x1": 3.0, "y1": 4.0} for k in range(3)])
             if i % 2 == 0 else None}
            for i in range(12)]


def _expected(rows):
    return [{"index": r["index"], "text": r["text"], "page": r["page"], "x0": r["x0"], "y0": r["y0"],
             "x1": r["x1"], "y1": r["y1"], "filtered": r["filtered"],
             "words": json.loads(r["words"]) if r["words"] else [],
             "chapter": r["chapter"], "chapter_title": r["chapter_title"]} for r in rows]


def test_payload_unchanged(client, db_engine, seed_book):
    rows = _rows()
    seed_book(db_engine, sentences=rows, ready_generation=3)
    r = client.get(f"/documents/{BID}/sentences?generation=3", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers["x-render-generation"] == "3"
    assert r.headers["content-type"].startswith("application/json")
    assert "content-encoding" not in r.headers
    assert json.loads(r.content) == _expected(rows)


def test_gzip_when_accepted(client, db_engine, seed_book):
    rows = _rows()
    seed_book(db_engine, sentences=rows)
    r = client.get(f"/documents/{BID}/sentences", headers={"Accept-Encoding": "gzip, br"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in r.headers["vary"].lower()
    assert r.json() == _expected(rows)  # httpx decoded it
    raw = client.get(f"/documents/{BID}/sentences", headers={"Accept-Encoding": "gzip"})
    assert raw.headers["x-render-generation"] == "1"


def test_gzip_body_is_a_valid_gzip_stream(client, db_engine, seed_book):
    seed_book(db_engine, sentences=_rows())
    with client.stream("GET", f"/documents/{BID}/sentences",
                       headers={"Accept-Encoding": "gzip"}) as r:
        wire = b"".join(r.iter_raw())
    assert json.loads(gzip.decompress(wire))[0]["index"] == 0


def test_409_and_404_kept(client, db_engine, seed_book):
    seed_book(db_engine, ready_generation=None)
    assert client.get(f"/documents/{BID}/sentences").status_code == 409
    assert client.get("/documents/nope/sentences").status_code == 404
