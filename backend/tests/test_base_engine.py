"""TDD tests for base engine abstraction."""
import pytest
from services.base_engine import BaseEngine, SentenceRecord


@pytest.fixture
def base_engine():
    return BaseEngine()


class TestSentenceRecord:
    def test_has_required_fields(self):
        record = SentenceRecord(index=0, text="Hello world.")
        assert record.index == 0
        assert record.text == "Hello world."
        assert record.page == 0
        assert record.x0 == 0.0
        assert record.y0 == 0.0
        assert record.x1 == 0.0
        assert record.y1 == 0.0


class TestBaseEngine:
    def test_init_loads_spacy_model(self, base_engine):
        assert base_engine.nlp is not None

    def test_split_sentences_basic(self, base_engine):
        text = "This is the first sentence. This is the second sentence."
        sentences = base_engine._split_sentences(text)
        assert len(sentences) == 2
        assert sentences[0] == "This is the first sentence."
        assert sentences[1] == "This is the second sentence."

    def test_split_sentences_with_question_and_exclamation(self, base_engine):
        text = "Is this a question? This is an exclamation! This is a period."
        sentences = base_engine._split_sentences(text)
        assert len(sentences) == 3

    def test_split_sentences_min_words_filter(self, base_engine):
        text = "Hi there. This is a proper sentence."
        sentences = base_engine._split_sentences(text, min_words=3)
        assert len(sentences) == 1  # "Hi there." has only 2 words, filtered out
        assert sentences[0] == "This is a proper sentence."

    def test_split_sentences_empty_text(self, base_engine):
        text = ""
        sentences = base_engine._split_sentences(text)
        assert sentences == []

    def test_split_sentences_whitespace_only(self, base_engine):
        text = "   \n  \t  "
        sentences = base_engine._split_sentences(text)
        assert sentences == []

    def test_split_sentences_two_word_not_filtered(self, base_engine):
        text = "Hi there. Another sentence."
        sentences = base_engine._split_sentences(text, min_words=2)
        assert len(sentences) == 2  # Both have 2 words, not filtered with min_words=2

    def test_split_sentences_preserves_whitespace(self, base_engine):
        text = "  This is the first sentence.   This is the second sentence.  "
        sentences = base_engine._split_sentences(text)
        assert sentences[0] == "This is the first sentence."
        assert sentences[1] == "This is the second sentence."
