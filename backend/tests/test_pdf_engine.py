"""TDD tests for services/pdf_engine.py.

Requires ``~/Documents/EBooks/cleancodebook.pdf`` (see tests/README.md).

The extraction runs **once per module**. Parsing the 462-page book with PyMuPDF
and spaCy takes about a minute on this machine, and the old version asked for a
fresh extraction in each of ten tests -- roughly eleven minutes of wall clock for
these assertions alone. That is what made the full suite look like it had hung.
No assertion was relaxed: every test still inspects the real extraction result,
it just shares one.
"""
from pathlib import Path

import pytest

from services.base_engine import SentenceRecord
from services.pdf_engine import PDFEngine, sentence_bbox

CLEAN_CODE_PDF = Path.home() / "Documents/EBooks" / "cleancodebook.pdf"


@pytest.fixture(scope="module")
def clean_code_pdf():
    if not CLEAN_CODE_PDF.exists():
        pytest.skip(f"{CLEAN_CODE_PDF} not found (see tests/README.md)")
    return CLEAN_CODE_PDF


@pytest.fixture(scope="module")
def engine():
    return PDFEngine()


@pytest.fixture(scope="module")
def sentences(engine, clean_code_pdf):
    """The parsed book, shared by every test in this module."""
    return engine.extract_sentences(str(clean_code_pdf))


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
    def test_returns_list(self, sentences):
        assert isinstance(sentences, list)

    def test_returns_sentence_records(self, sentences):
        assert len(sentences) > 0
        assert all(isinstance(s, SentenceRecord) for s in sentences)

    def test_sentences_have_text(self, sentences):
        assert all(len(s.text.strip()) > 0 for s in sentences)

    def test_indices_are_sequential(self, sentences):
        for i, s in enumerate(sentences):
            assert s.index == i

    def test_page_numbers_valid(self, sentences):
        assert all(s.page >= 0 for s in sentences)

    def test_bboxes_are_positive(self, sentences):
        assert all(s.x0 >= 0 and s.y0 >= 0 for s in sentences)
        assert all(s.x1 > s.x0 and s.y1 > s.y0 for s in sentences)

    def test_finds_expected_prose(self, sentences):
        texts = " ".join(s.text for s in sentences)
        assert "clean code" in texts.lower()

    def test_raises_on_missing_file(self, engine):
        with pytest.raises(FileNotFoundError):
            engine.extract_sentences("/nonexistent/path.pdf")

    def test_sentences_include_words_field(self, sentences):
        assert all(hasattr(s, 'words') and isinstance(s.words, list) for s in sentences)

    def test_single_word_sentence_bbox_is_tight(self, sentences):
        single_word_sentences = [s for s in sentences if len(s.words) == 1]
        if not single_word_sentences:
            pytest.skip("No single word sentences found for testing")
        s = single_word_sentences[0]
        expected_bbox = (s.words[0]['x0'], s.words[0]['y0'], s.words[0]['x1'], s.words[0]['y1'])
        assert (s.x0, s.y0, s.x1, s.y1) == expected_bbox


class TestPageCount:
    def test_returns_page_count(self, engine, clean_code_pdf):
        count = engine.page_count(str(clean_code_pdf))
        assert count > 0
        assert isinstance(count, int)


class TestIsImagePage:
    def test_text_page_is_not_image_page(self, engine, clean_code_pdf):
        # Page 10+ of Clean Code has real text
        assert not engine.is_image_page(str(clean_code_pdf), page_num=10)


class TestSentenceBbox:
    def test_empty_words_returns_block_bbox(self):
        block_bbox = (10.0, 20.0, 300.0, 50.0)
        result = sentence_bbox([], block_bbox)
        assert result == block_bbox

    def test_single_word_uses_word_bbox(self):
        sent_words = [(50.0, 20.0, 150.0, 35.0, "word")]
        block_bbox = (10.0, 20.0, 300.0, 50.0)
        result = sentence_bbox(sent_words, block_bbox)
        assert result == (50.0, 20.0, 150.0, 35.0)

    def test_multiple_words_computes_min_max_bbox(self):
        sent_words = [
            (50.0, 20.0, 70.0, 35.0, "word1"),
            (80.0, 25.0, 110.0, 40.0, "word2"),
            (120.0, 15.0, 160.0, 45.0, "word3")
        ]
        block_bbox = (10.0, 20.0, 300.0, 50.0)
        result = sentence_bbox(sent_words, block_bbox)
        assert result == (50.0, 15.0, 160.0, 45.0)

    def test_words_varying_coords_compute_correct_bbox(self):
        sent_words = [
            (100.0, 30.0, 120.0, 40.0, "first"),
            (90.0, 25.0, 130.0, 35.0, "middle"),
            (110.0, 35.0, 140.0, 50.0, "last")
        ]
        block_bbox = (0.0, 0.0, 200.0, 100.0)
        result = sentence_bbox(sent_words, block_bbox)
        assert result == (90.0, 25.0, 140.0, 50.0)
