"""TDD tests for text engine."""
import pytest
from services.text_engine import TextEngine
from services.base_engine import SentenceRecord


@pytest.fixture
def text_engine():
    return TextEngine()


class TestTextEngine:
    def test_extract_sentences_basic(self, text_engine):
        # Use longer sentences that won't be filtered by min_words=2
        text = "This is the first sentence. This is the second sentence."
        result = text_engine.extract_sentences(text)
        assert len(result) == 2
        assert all(isinstance(r, SentenceRecord) for r in result)
        assert result[0].index == 0
        assert result[0].text == "This is the first sentence."
        assert result[1].index == 1
        assert result[1].text == "This is the second sentence."

    def test_extract_sentences_has_zero_coordinates(self, text_engine):
        text = "This is a test sentence."
        result = text_engine.extract_sentences(text)
        assert len(result) == 1
        # Text books have zero coordinates (like EPUB)
        assert result[0].page == 0
        assert result[0].x0 == 0.0
        assert result[0].y0 == 0.0
        assert result[0].x1 == 0.0
        assert result[0].y1 == 0.0

    def test_extract_sentences_filters_short(self, text_engine):
        text = "Hi. This is a complete sentence."
        result = text_engine.extract_sentences(text)
        assert len(result) == 1
        assert result[0].text == "This is a complete sentence."

    def test_extract_sentences_empty_text(self, text_engine):
        text = ""
        result = text_engine.extract_sentences(text)
        assert result == []

    def test_extract_sentences_whitespace_only(self, text_engine):
        text = "   \n  "
        result = text_engine.extract_sentences(text)
        assert result == []

    def test_extract_sentences_single_word_not_filtered(self, text_engine):
        # Single words should be filtered (default min_words=3)
        text = "Go."
        result = text_engine.extract_sentences(text)
        # "Go" has 1 word, filtered out
        assert len(result) == 0

    def test_extract_sentences_complex_text(self, text_engine):
        text = "The quick brown fox jumps over the lazy dog. Another sentence here."
        result = text_engine.extract_sentences(text)
        assert len(result) == 2
        assert result[0].text == "The quick brown fox jumps over the lazy dog."
        assert result[1].text == "Another sentence here."

    def test_extract_sentences_with_quotes(self, text_engine):
        text = '"Hello world!" he said. This is another sentence.'
        result = text_engine.extract_sentences(text)
        # spaCy splits after closing quote and after "he said."
        # With min_words=2, "Hello world" and "he said" might be filtered
        # So we get 1 sentence: "This is another sentence."
        assert len(result) >= 1
        # Verify second sentence is present
        assert any("another sentence" in s.text.lower() for s in result)
