"""Tests for the bookmarks router.

The ``db_engine``/``client`` fixtures come from conftest.py; the copy that used to
live here leaked ``app.dependency_overrides`` and its own engine into later files.
"""
from db.models import Bookmark
from sqlmodel import Session, select


def _upload(client):
    response = client.post(
        "/documents/upload",
        files={"file": ("test.pdf", b"fakepdf", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["book_id"]


class TestCreateBookmark:
    def test_create_with_valid_book_returns_bookmark(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={
            "book_id": bid, "sentence_index": 2, "label": "Test bookmark"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["book_id"] == bid
        assert data["sentence_index"] == 2
        assert data["label"] == "Test bookmark"
        assert data["page"] >= 0

    def test_create_nonexistent_book_returns_404(self, client):
        r = client.post("/bookmarks", json={
            "book_id": "nonexistent", "sentence_index": 0, "label": "Bad"
        })
        assert r.status_code == 404

    def test_label_truncated_to_100_chars(self, client):
        bid = _upload(client)
        long_label = "x" * 200
        r = client.post("/bookmarks", json={
            "book_id": bid, "sentence_index": 0, "label": long_label
        })
        assert len(r.json()["label"]) == 100


class TestListBookmarks:
    def test_list_empty_returns_empty_list(self, client):
        bid = _upload(client)
        r = client.get(f"/bookmarks/{bid}")
        assert r.json() == []

    def test_list_returns_bookmarks_in_order(self, client, db_engine):
        """The list endpoint orders by (page, sentence_index), not insertion order."""
        bid = _upload(client)
        client.post("/bookmarks", json={"book_id": bid, "sentence_index": 3, "label": "Third"})
        client.post("/bookmarks", json={"book_id": bid, "sentence_index": 1, "label": "First"})

        rows = client.get(f"/bookmarks/{bid}").json()
        assert [row["sentence_index"] for row in rows] == [1, 3]
        assert [row["label"] for row in rows] == ["First", "Third"]

        # ...and that matches what the database holds, in the same order.
        with Session(db_engine) as session:
            stored = session.exec(
                select(Bookmark).where(Bookmark.book_id == bid).order_by(Bookmark.sentence_index)
            ).all()
        assert [row.sentence_index for row in stored] == [1, 3]


class TestDeleteBookmark:
    def test_delete_existing_returns_200(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={"book_id": bid, "sentence_index": 0, "label": "X"})
        bm_id = r.json()["id"]
        r2 = client.delete(f"/bookmarks/{bm_id}")
        assert r2.status_code == 200

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/bookmarks/99999")
        assert r.status_code == 404

    def test_delete_removes_from_list(self, client):
        bid = _upload(client)
        r = client.post("/bookmarks", json={"book_id": bid, "sentence_index": 0, "label": "X"})
        bm_id = r.json()["id"]
        client.delete(f"/bookmarks/{bm_id}")
        bookmarks = client.get(f"/bookmarks/{bid}").json()
        ids = [b["id"] for b in bookmarks]
        assert bm_id not in ids
