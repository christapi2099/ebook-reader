"""Tests for DELETE /library/{book_id} endpoint.

The ``db_engine``/``client`` fixtures come from conftest.py.
"""


def _upload(client, filename="test.pdf", content=b"fakepdf"):
    response = client.post(
        "/documents/upload",
        files={"file": (filename, content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestDeleteBook:
    def test_delete_existing_book_returns_200(self, client):
        data = _upload(client)
        r = client.delete(f"/library/{data['book_id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_removes_book_from_library(self, client):
        data = _upload(client)
        client.delete(f"/library/{data['book_id']}")
        books = client.get("/library").json()
        ids = [b["id"] for b in books]
        assert data["book_id"] not in ids

    def test_delete_removes_progress(self, client):
        data = _upload(client)
        bid = data["book_id"]
        client.post(f"/library/{bid}/progress", json={"sentence_index": 10})
        client.delete(f"/library/{bid}")
        r = client.get(f"/library/{bid}/progress")
        assert r.json()["sentence_index"] == 0

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/library/nonexistent")
        assert r.status_code == 404

    def test_delete_sentences_removed(self, client):
        data = _upload(client)
        bid = data["book_id"]
        assert len(client.get(f"/documents/{bid}/sentences").json()) == 5
        client.delete(f"/library/{bid}")
        r = client.get(f"/documents/{bid}/sentences")
        assert r.status_code == 404
