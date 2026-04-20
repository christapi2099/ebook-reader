"""TDD tests for services/pdf_engine.py."""
import pytest
from pathlib import Path
from services.pdf_engine import PDFEngine, SentenceRecord


@pytest.fixture
def engine():
    return PDFEngine()


@pytest.fixture
def clean_code_pdf():
    return Path.home() / "Documents/EBooks/cleancodebook.pdf"


class TestSentenceRecord:
    def test_has_required_fields(self):
        s = SentenceRecord(
            index=0, text="Hello world.", page=0,
            x0=10.0, y0=20.0, x1=200.0, y1=35.0
        )
        assert s.text == "Hello world."
        assert s.page == 0
        assert s.index == 0

    def test_bbox_fields_are_floats(self):
        s = SentenceRecord(index=0, text="Test.", page=1, x0=0.0, y0=0.0, x1=1.0, y1=1.0)
        for field in (s.x0, s.y0, s.x1, s.y1):
            assert isinstance(field, float)


class TestExtractSentences:
    def test_returns_list(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        assert isinstance(result, list)

    def test_returns_sentence_records(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        assert len(result) > 0
        assert all(isinstance(s, SentenceRecord) for s in result)

    def test_sentences_have_text(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        assert all(len(s.text.strip()) > 0 for s in result)

    def test_indices_are_sequential(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        for i, s in enumerate(result):
            assert s.index == i

    def test_page_numbers_valid(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        assert all(s.page >= 0 for s in result)

    def test_bboxes_are_positive(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        assert all(s.x0 >= 0 and s.y0 >= 0 for s in result)
        assert all(s.x1 > s.x0 and s.y1 > s.y0 for s in result)

    def test_finds_expected_prose(self, engine, clean_code_pdf):
        result = engine.extract_sentences(str(clean_code_pdf))
        texts = " ".join(s.text for s in result)
        assert "clean code" in texts.lower()

    def test_raises_on_missing_file(self, engine):
        with pytest.raises(FileNotFoundError):
            engine.extract_sentences("/nonexistent/path.pdf")


class TestPageCount:
    def test_returns_page_count(self, engine, clean_code_pdf):
        count = engine.page_count(str(clean_code_pdf))
        assert count > 0
        assert isinstance(count, int)


class TestIsImagePage:
    def test_text_page_is_not_image_page(self, engine, clean_code_pdf):
        # Page 10+ of Clean Code has real text
        assert not engine.is_image_page(str(clean_code_pdf), page_num=10)
