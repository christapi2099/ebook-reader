"""TDD tests for epub_engine.py and ocr_engine.py."""
import pytest
from pathlib import Path
from services.epub_engine import EPUBEngine, SentenceRecord as EpubSentenceRecord
from services.ocr_engine import OCREngine


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
        assert all(isinstance(s, EpubSentenceRecord) for s in result)

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

    def test_sentence_record_has_chapter(self, engine):
        result = engine.extract_sentences(str(EPUB_PATH))
        assert hasattr(result[0], 'chapter')


class TestOCREngine:
    @pytest.fixture
    def engine(self):
        return OCREngine()

    def test_engine_initializes(self, engine):
        assert engine is not None

    def test_process_image_returns_sentences(self, engine):
        import fitz
        doc = fitz.open(str(EPUB_PATH.parent / "cleancodebook.pdf"))
        page = doc[20]
        result = engine.process_page(page)
        doc.close()
        assert isinstance(result, list)

    def test_is_available(self, engine):
        assert engine.is_available()
