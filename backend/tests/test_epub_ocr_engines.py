"""TDD tests for epub_engine.py (and the shared sentence-record contract).

Requires ``~/Documents/EBooks/cleancodebook.epub`` (see tests/README.md).

The book is parsed **once per module**. Reader + spaCy over the whole EPUB takes
about a minute, and the old version re-parsed it in all seven tests; the suite
spent minutes here and looked like it had hung. The assertions are unchanged.
"""
from pathlib import Path

import pytest

from services.base_engine import SentenceRecord as BaseSentenceRecord
from services.epub_engine import EPUBEngine

EPUB_PATH = Path.home() / "Documents/EBooks/cleancodebook.epub"


@pytest.fixture(scope="module")
def epub_path():
    if not EPUB_PATH.exists():
        pytest.skip(f"{EPUB_PATH} not found (see tests/README.md)")
    return EPUB_PATH


@pytest.fixture(scope="module")
def engine():
    return EPUBEngine()


@pytest.fixture(scope="module")
def sentences(engine, epub_path):
    """The parsed book, shared by every test in this module."""
    return engine.extract_sentences(str(epub_path))


class TestEPUBEngine:
    def test_extract_returns_list(self, sentences):
        assert isinstance(sentences, list)

    def test_returns_sentence_records(self, sentences):
        assert len(sentences) > 0
        # Verify all records are BaseSentenceRecord instances
        assert all(isinstance(s, BaseSentenceRecord) for s in sentences)

    def test_sentences_have_text(self, sentences):
        assert all(len(s.text.strip()) > 0 for s in sentences)

    def test_indices_sequential(self, sentences):
        for i, s in enumerate(sentences):
            assert s.index == i

    def test_finds_prose(self, sentences):
        texts = " ".join(s.text for s in sentences)
        assert "clean code" in texts.lower()

    def test_raises_on_missing_file(self, engine):
        with pytest.raises(FileNotFoundError):
            engine.extract_sentences("/nonexistent/book.epub")

    def test_sentence_record_has_required_fields(self, sentences):
        """Verify sentence records have the fields needed by documents.py router."""
        assert len(sentences) > 0
        # The router expects index and text fields
        assert hasattr(sentences[0], 'index')
        assert hasattr(sentences[0], 'text')
        # EPUB sentences have page=0 and zero coordinates
        assert hasattr(sentences[0], 'page')
        assert hasattr(sentences[0], 'x0')
        assert sentences[0].page == 0
        assert sentences[0].x0 == 0.0
