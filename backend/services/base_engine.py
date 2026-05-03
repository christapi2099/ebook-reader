"""Shared base engine abstraction for sentence extraction."""
from dataclasses import dataclass
import spacy


@dataclass
class SentenceRecord:
    index: int
    text: str
    page: int = 0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    filtered: bool = False


class BaseEngine:
    """Base class for sentence extraction engines using spaCy."""

    def __init__(self):
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError:
            # Fallback to en_core_web_sm if not available
            import subprocess
            subprocess.run(
                ["python", "-m", "spacy", "download", "en_core_web_sm"],
                capture_output=True
            )
            self.nlp = spacy.load("en_core_web_sm")

    def _split_sentences(self, text: str, min_words: int = 3) -> list[str]:
        """Split text into sentences using spaCy, filtering short fragments."""
        if not text or text.isspace():
            return []
        
        doc = self.nlp(text)
        sentences = []
        for sent in doc.sents:
            text_sent = sent.text.strip()
            if not text_sent:
                continue
            # Count words (skip punctuation)
            word_count = len([t for t in sent if not t.is_punct])
            if word_count >= min_words:
                sentences.append(text_sent)
        return sentences
