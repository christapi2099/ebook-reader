"""TDD tests for FastAPI routers. PDF/EPUB engines are mocked for speed.

The ``db_engine``, ``client``, ``mock_engines`` and ``upload_book`` fixtures come
from conftest.py; this file used to carry its own copy-pasted pair, which left
``db.database.engine`` pointing at a dead database for every later test file.
"""
import pytest


def _upload(client, filename="test.pdf", content=b"fakepdfbytes"):
    response = client.post(
        "/documents/upload",
        files={"file": (filename, content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestUploadDocument:
    def test_upload_pdf_returns_200_and_a_book_id(self, client):
        response = _upload(client)
        assert "book_id" in response
        assert len(response["book_id"]) == 64

    @pytest.mark.parametrize("sentence_count", [3, 5])
    def test_upload_reports_and_stores_one_row_per_extracted_sentence(
        self, client, mock_engines, upload_book, sentence_count
    ):
        """The count in the response must be the number of sentences actually
        persisted, not just whatever the engine was configured to return."""
        from services.base_engine import SentenceRecord

        mock_engines.pdf.return_value.extract_sentences.return_value = [
            SentenceRecord(index=i, text=f"Sentence number {i}.", page=0) for i in range(sentence_count)
        ]

        response = upload_book()
        assert response["sentence_count"] == sentence_count

        rows = client.get(f"/documents/{response['book_id']}/sentences").json()
        assert len(rows) == sentence_count
        assert [row["index"] for row in rows] == list(range(sentence_count))

    def test_duplicate_upload_returns_same_id(self, client):
        ids = [_upload(client, content=b"samebytes")["book_id"] for _ in range(2)]
        assert ids[0] == ids[1]

    def test_duplicate_sets_already_existed(self, client):
        _upload(client, content=b"dupbytes")
        r2 = _upload(client, content=b"dupbytes")
        assert r2["already_existed"] is True

    def test_unsupported_type_returns_400(self, client):
        r = client.post("/documents/upload", files={"file": ("book.txt", b"text", "text/plain")})
        assert r.status_code == 400


class TestGetSentences:
    @pytest.fixture
    def book_id(self, client):
        return _upload(client)["book_id"]

    def test_get_sentences_returns_list(self, client, book_id):
        r = client.get(f"/documents/{book_id}/sentences")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_sentences_have_required_fields(self, client, book_id):
        r = client.get(f"/documents/{book_id}/sentences")
        s = r.json()[0]
        for field in ("index", "text", "page", "x0", "y0", "x1", "y1", "filtered"):
            assert field in s

    def test_unknown_book_returns_404(self, client):
        assert client.get("/documents/nonexistent/sentences").status_code == 404


class TestLibrary:
    def test_list_library_returns_list(self, client):
        _upload(client)
        r = client.get("/library")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_library_contains_uploaded_book(self, client):
        book_id = _upload(client)["book_id"]
        ids = [b["id"] for b in client.get("/library").json()]
        assert book_id in ids


class TestProgress:
    @pytest.fixture
    def book_id(self, client):
        return _upload(client)["book_id"]

    def test_save_progress(self, client, book_id):
        r = client.post(f"/library/{book_id}/progress", json={"sentence_index": 42})
        assert r.status_code == 200

    def test_get_progress(self, client, book_id):
        client.post(f"/library/{book_id}/progress", json={"sentence_index": 3})
        r = client.get(f"/library/{book_id}/progress")
        assert r.status_code == 200
        assert r.json()["sentence_index"] == 3

    def _last_index(self, client, book_id):
        return len(client.get(f"/documents/{book_id}/sentences").json()) - 1

    def test_index_past_the_end_is_clamped_to_the_last_sentence(self, client, book_id):
        last = self._last_index(client, book_id)
        r = client.post(f"/library/{book_id}/progress", json={"sentence_index": last + 1000})
        assert r.json()["sentence_index"] == last
        assert client.get(f"/library/{book_id}/progress").json()["sentence_index"] == last

    def test_negative_index_is_clamped_to_zero(self, client, book_id):
        r = client.post(f"/library/{book_id}/progress", json={"sentence_index": -7})
        assert r.json()["sentence_index"] == 0
        assert client.get(f"/library/{book_id}/progress").json()["sentence_index"] == 0

    def test_in_range_index_is_stored_unchanged(self, client, book_id):
        last = self._last_index(client, book_id)
        assert last >= 1, "the fixture book needs two sentences for this to mean anything"
        r = client.post(f"/library/{book_id}/progress", json={"sentence_index": last})
        assert r.json()["sentence_index"] == last

    def test_no_progress_returns_zero(self, client, book_id):
        r = client.get(f"/library/{book_id}/progress")
        assert r.json()["sentence_index"] == 0
