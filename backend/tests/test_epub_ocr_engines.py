"""TDD tests for epub_engine.py and ocr_engine.py."""
import pytest
from pathlib import Path
from services.epub_engine import EPUBEngine
from services.base_engine import SentenceRecord as BaseSentenceRecord


EPUB_PATH = Path.home() / "Documents/EBooks/cleancodebook.epub"


class TestEPUBEngine:
    @pytest.fixture
    def engine(self):
        return EPUBEngine()

    def test_extract_returns_list(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        assert isinstance(result, list)

    def test_returns_sentence_records(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        assert len(result) > 0
        # Verify all records are BaseSentenceRecord instances
        assert all(isinstance(s, BaseSentenceRecord) for s in result)

    def test_sentences_have_text(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        assert all(len(s.text.strip()) > 0 for s in result)

    def test_indices_sequential(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        for i, s in enumerate(result):
            assert s.index == i

    def test_finds_prose(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        texts = " ".join(s.text for s in result)
        assert "clean code" in texts.lower()

    def test_raises_on_missing_file(self, engine):
        with pytest.raises(FileNotFoundError):
            engine.extract_sentences("/nonexistent/book.epub")

    def test_sentence_record_has_required_fields(self, engine):
        """Verify sentence records have the fields needed by documents.py router."""
        result = engine.extract_sentences(str(EPUB_PATH))
        assert len(result) > 0
        # The router expects index and text fields
        assert hasattr(result[0], 'index')
        assert hasattr(result[0], 'text')
        # EPUB sentences have page=0 and zero coordinates
        assert hasattr(result[0], 'page')
        assert hasattr(result[0], 'x0')
        assert result[0].page == 0
        assert result[0].x0 == 0.0
