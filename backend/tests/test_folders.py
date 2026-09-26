"""Tests for the /folders endpoints and POST /library/{book_id}/folder.

These drive the real FastAPI app with an in-memory database. `TestClient` is
deliberately NOT used as a context manager: entering it runs the app lifespan,
which calls `create_engine_and_tables()` against the real 800 MB
`backend/ebook_reader.db` and loads Kokoro. None of that is needed to exercise
these endpoints, and avoiding it keeps the suite from mutating a developer's
library.
"""
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import db.database as _db
from db.database import get_session
from db.models import FOLDER_NAME_MAX_LENGTH, Book
from main import app


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db_engine):
    def override_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


@pytest.fixture
def add_book(db_engine):
    def _add(book_id="bk1", title="A Book", ephemeral=False):
        with Session(db_engine) as session:
            session.add(Book(
                id=book_id,
                title=title,
                file_path=f"/tmp/{book_id}.pdf",
                file_type="pdf",
                page_count=10,
                created_at=datetime.now(timezone.utc),
                ephemeral=ephemeral,
            ))
            session.commit()
        return book_id

    return _add


class TestCreateFolder:
    def test_creates_folder(self, client):
        r = client.post("/folders", json={"name": "Reading"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Reading"
        assert body["book_count"] == 0
        assert isinstance(body["id"], int)

    def test_new_folder_appears_in_listing(self, client):
        client.post("/folders", json={"name": "Reading"})
        assert [f["name"] for f in client.get("/folders").json()] == ["Reading"]

    def test_empty_listing_when_no_folders(self, client):
        assert client.get("/folders").json() == []

    @pytest.mark.parametrize("name", ["", "   ", "\n\t "])
    def test_blank_name_is_rejected(self, client, name):
        assert client.post("/folders", json={"name": name}).status_code == 400

    def test_name_is_trimmed(self, client):
        r = client.post("/folders", json={"name": "  Reading  "})
        assert r.json()["name"] == "Reading"

    def test_duplicate_name_is_rejected(self, client):
        client.post("/folders", json={"name": "Reading"})
        assert client.post("/folders", json={"name": "Reading"}).status_code == 409

    def test_duplicate_check_is_case_insensitive(self, client):
        client.post("/folders", json={"name": "Reading"})
        assert client.post("/folders", json={"name": "rEaDiNg"}).status_code == 409

    def test_name_at_the_length_cap_is_allowed(self, client):
        r = client.post("/folders", json={"name": "x" * FOLDER_NAME_MAX_LENGTH})
        assert r.status_code == 201

    def test_name_over_the_length_cap_is_rejected(self, client):
        r = client.post("/folders", json={"name": "x" * (FOLDER_NAME_MAX_LENGTH + 1)})
        assert r.status_code == 400


class TestRenameFolder:
    def test_renames(self, client):
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        r = client.patch(f"/folders/{folder_id}", json={"name": "Finished"})
        assert r.status_code == 200
        assert r.json()["name"] == "Finished"
        assert [f["name"] for f in client.get("/folders").json()] == ["Finished"]

    def test_blank_rename_is_rejected(self, client):
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        assert client.patch(f"/folders/{folder_id}", json={"name": " "}).status_code == 400

    def test_rename_to_another_folders_name_is_rejected(self, client):
        client.post("/folders", json={"name": "Reading"})
        other = client.post("/folders", json={"name": "Finished"}).json()["id"]
        assert client.patch(f"/folders/{other}", json={"name": "reading"}).status_code == 409

    def test_rename_to_own_name_is_allowed(self, client):
        """Recasing a folder must not trip the duplicate check against itself."""
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        r = client.patch(f"/folders/{folder_id}", json={"name": "READING"})
        assert r.status_code == 200
        assert r.json()["name"] == "READING"

    def test_rename_missing_folder_returns_404(self, client):
        assert client.patch("/folders/999", json={"name": "Nope"}).status_code == 404


class TestDeleteFolder:
    def test_delete_removes_the_folder(self, client):
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        assert client.delete(f"/folders/{folder_id}").status_code == 200
        assert client.get("/folders").json() == []

    def test_delete_missing_folder_returns_404(self, client):
        assert client.delete("/folders/999").status_code == 404

    def test_delete_keeps_its_books(self, client, add_book):
        """The handoff's rule: deleting a folder must never delete books."""
        book_id = add_book()
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post(f"/library/{book_id}/folder", json={"folder_id": folder_id})

        r = client.delete(f"/folders/{folder_id}")
        assert r.json()["unfiled_books"] == 1

        books = client.get("/library").json()
        assert [b["id"] for b in books] == [book_id]
        assert books[0]["folder_id"] is None

    def test_delete_reports_zero_when_no_books_were_filed(self, client):
        folder_id = client.post("/folders", json={"name": "Empty"}).json()["id"]
        assert client.delete(f"/folders/{folder_id}").json()["unfiled_books"] == 0


class TestAssignBookToFolder:
    def test_files_a_book(self, client, add_book):
        book_id = add_book()
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]

        r = client.post(f"/library/{book_id}/folder", json={"folder_id": folder_id})
        assert r.status_code == 200
        assert r.json()["folder_id"] == folder_id

    def test_folder_listing_counts_its_books(self, client, add_book):
        add_book("bk1")
        add_book("bk2")
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post("/library/bk1/folder", json={"folder_id": folder_id})
        client.post("/library/bk2/folder", json={"folder_id": folder_id})

        folder = client.get("/folders").json()[0]
        assert folder["book_count"] == 2

    def test_book_listing_exposes_folder_id(self, client, add_book):
        """The UI cannot filter by folder unless the listing carries it."""
        book_id = add_book()
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post(f"/library/{book_id}/folder", json={"folder_id": folder_id})

        assert client.get("/library").json()[0]["folder_id"] == folder_id

    def test_null_unfiles_the_book(self, client, add_book):
        book_id = add_book()
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post(f"/library/{book_id}/folder", json={"folder_id": folder_id})

        r = client.post(f"/library/{book_id}/folder", json={"folder_id": None})
        assert r.json()["folder_id"] is None
        assert client.get("/folders").json()[0]["book_count"] == 0

    def test_omitted_folder_id_unfiles_the_book(self, client, add_book):
        """The handoff's "set/clear" endpoint: an empty body means clear."""
        book_id = add_book()
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post(f"/library/{book_id}/folder", json={"folder_id": folder_id})

        assert client.post(f"/library/{book_id}/folder", json={}).json()["folder_id"] is None

    def test_assigning_to_a_missing_folder_returns_404(self, client, add_book):
        book_id = add_book()
        r = client.post(f"/library/{book_id}/folder", json={"folder_id": 999})
        assert r.status_code == 404

    def test_assigning_a_missing_book_returns_404(self, client):
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        r = client.post("/library/nope/folder", json={"folder_id": folder_id})
        assert r.status_code == 404


class TestFolderListing:
    def test_listing_is_sorted_by_name(self, client):
        for name in ("Zebra", "apple", "Mango"):
            client.post("/folders", json={"name": name})
        names = [f["name"] for f in client.get("/folders").json()]
        assert names == sorted(names, key=str.lower)

    def test_ephemeral_books_are_not_counted(self, client, add_book):
        add_book("ephemeral", ephemeral=True)
        folder_id = client.post("/folders", json={"name": "Reading"}).json()["id"]
        client.post("/library/ephemeral/folder", json={"folder_id": folder_id})

        assert client.get("/folders").json()[0]["book_count"] == 0


class TestFoldersMigration:
    """`book.folder_id` must reach databases that predate folders.

    `create_all()` creates the new `folder` table on an existing database but
    never adds a column to a table that already exists, so an existing library —
    the user's is 800 MB and predates this feature — depends entirely on
    `_migrate()` adding it. That migration was once silently lost to a concurrent
    edit, so it is pinned here rather than trusted.
    """

    @staticmethod
    def _legacy_db(tmp_path):
        """A database whose `book` table predates the folder feature."""
        db_file = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE book (id TEXT PRIMARY KEY, title TEXT, author TEXT, "
            "file_path TEXT, file_type TEXT, page_count INTEGER, cover_page INTEGER, "
            "created_at TIMESTAMP, last_opened TIMESTAMP, ephemeral BOOLEAN)"
        )
        conn.execute(
            "INSERT INTO book VALUES ('b1','Old Book',NULL,'/x.pdf','pdf',5,0,"
            "'2024-01-01',NULL,0)"
        )
        conn.commit()
        conn.close()
        return db_file

    @staticmethod
    def _snapshot(engine):
        with engine.connect() as conn:
            tables = sorted(inspect(engine).get_table_names())
            return {
                table: [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
                for table in tables
            }

    def test_adds_folder_id_to_a_preexisting_book_table(self, tmp_path):
        engine = _db.create_engine_and_tables(db_url=f"sqlite:///{self._legacy_db(tmp_path)}")
        columns = {c["name"] for c in inspect(engine).get_columns("book")}
        assert "folder_id" in columns

    def test_preserves_existing_rows_and_leaves_them_unfiled(self, tmp_path):
        engine = _db.create_engine_and_tables(db_url=f"sqlite:///{self._legacy_db(tmp_path)}")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, title, folder_id FROM book")).fetchall()
        assert rows == [("b1", "Old Book", None)]

    def test_creates_the_folder_table_and_its_index(self, tmp_path):
        engine = _db.create_engine_and_tables(db_url=f"sqlite:///{self._legacy_db(tmp_path)}")
        assert "folder" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            indexes = {row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'"))}
        assert "ix_book_folder_id" in indexes

    def test_migration_is_idempotent(self, tmp_path):
        engine = _db.create_engine_and_tables(db_url=f"sqlite:///{self._legacy_db(tmp_path)}")
        first = self._snapshot(engine)
        _db._migrate(engine)
        _db._migrate(engine)
        assert self._snapshot(engine) == first
